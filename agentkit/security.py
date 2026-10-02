"""Security primitives: PII redaction, rate limiting, authentication (JWT / trusted proxy / API keys),
group → access-level mapping, encrypted logs with retention, and the ingestion domain allowlist.

Mapped to OWASP Top 10 for LLM Applications 2025: LLM02 (sensitive data), LLM04 (poisoning, via the
allowlist), LLM08 (access control on retrieval), LLM10 (unbounded consumption, via rate limits).
"""
import hashlib
import hmac
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import ROOT

# PII (LLM02)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_NATIONAL_ID = re.compile(r"\b[23]\d{13}\b")  # Egyptian national ID: 14 digits starting with century digit
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE = [re.compile(r"(?:\+?20|\b0)1[0125]\d{8}\b"), re.compile(r"\+\d[\d\s-]{8,}\d")]


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


def redact(text: str) -> str:
    """Remove emails, Egyptian national IDs, payment cards (Luhn-checked, so ISBNs survive) and phone numbers."""
    text = _EMAIL.sub("[EMAIL]", text)
    text = _NATIONAL_ID.sub("[NATIONAL_ID]", text)
    text = _CARD.sub(lambda m: "[CARD]" if _luhn(re.sub(r"\D", "", m.group())) else m.group(), text)
    for p in _PHONE:
        text = p.sub("[PHONE]", text)
    return text


def pseudonym(user: str) -> str:
    """Stable, non-reversible user id for logs (HMAC with a server secret), so logs hold no identities."""
    key = os.getenv("AGENTKIT_LOG_SALT", "agentkit-dev-salt").encode()
    return hmac.new(key, user.encode(), hashlib.sha256).hexdigest()[:16] if user else "anonymous"


# Rate limiting (LLM10)
class RateLimiter:
    """Token bucket per key (user or IP). `rate` like "30/min", "5/s", "500/hour"."""

    def __init__(self, rate: str = "30/min"):
        n, _, unit = rate.partition("/")
        self.capacity = float(n)
        self.per_second = self.capacity / {"s": 1, "sec": 1, "min": 60, "hour": 3600}[unit or "min"]
        self.buckets: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self.lock:
            tokens, last = self.buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.per_second)
            if tokens >= 1:
                self.buckets[key] = (tokens - 1, now)
                return True, 0.0
            self.buckets[key] = (tokens, now)
            return False, (1 - tokens) / self.per_second


# Authentication + access (LLM08)
@dataclass
class Principal:
    user: str = ""
    groups: list[str] = field(default_factory=list)
    access: tuple[str, ...] = ("public",)
    admin: bool = False

    @property
    def key(self) -> str:
        return self.user or "anonymous"


class AuthError(Exception):
    pass


def _access_config() -> dict:
    path = Path(os.getenv("AGENTKIT_ACCESS_CONFIG", ROOT / "config/access.json"))
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"default": ["public"], "groups": {}}


def access_for(groups: list[str]) -> tuple[str, ...]:
    cfg = _access_config()
    levels = set(cfg.get("default", ["public"]))
    for g in groups:
        levels.update(cfg.get("groups", {}).get(g, []))
    return tuple(sorted(levels))


def _admin_groups() -> set[str]:
    return {g.strip() for g in os.getenv("AGENTKIT_ADMIN_GROUPS", "library-staff-admin").split(",") if g.strip()}


def make_principal(user: str, groups: list[str], admin: bool = False) -> Principal:
    return Principal(user, groups, access_for(groups), admin or bool(_admin_groups() & set(groups)))


def verify_jwt(token: str) -> Principal:
    """Verify a bearer JWT from the university identity provider (OIDC id/access token).

    HS256 with AGENTKIT_JWT_SECRET, or RS256/ES256 with AGENTKIT_JWT_JWKS (URL or file path).
    Audience/issuer are enforced when AGENTKIT_JWT_AUDIENCE / AGENTKIT_JWT_ISSUER are set."""
    import jwt  # PyJWT
    opts = {"audience": os.getenv("AGENTKIT_JWT_AUDIENCE") or None, "issuer": os.getenv("AGENTKIT_JWT_ISSUER") or None}
    try:
        if os.getenv("AGENTKIT_JWT_SECRET"):
            claims = jwt.decode(token, os.environ["AGENTKIT_JWT_SECRET"], algorithms=["HS256"], **opts)
        elif os.getenv("AGENTKIT_JWT_JWKS"):
            src = os.environ["AGENTKIT_JWT_JWKS"]
            if src.startswith("https://"):
                key = jwt.PyJWKClient(src).get_signing_key_from_jwt(token).key
            else:
                jwks = jwt.PyJWKSet.from_json(Path(src).read_text())
                kid = jwt.get_unverified_header(token).get("kid")
                key = next(k.key for k in jwks.keys if kid in (None, k.key_id))
            claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], **opts)
        else:
            raise AuthError("JWT auth is not configured")
    except (jwt.PyJWTError, StopIteration) as e:
        raise AuthError(f"invalid token: {e}") from e
    groups = claims.get(os.getenv("AGENTKIT_JWT_GROUPS_CLAIM", "groups"), [])
    groups = groups.split(",") if isinstance(groups, str) else list(groups)
    return make_principal(str(claims.get("email") or claims.get("sub", "")), groups)


def _proxy_ok(h: dict[str, str]) -> bool:
    """Identity headers count only with the secret the SSO proxy adds, so a request that bypasses the proxy
    cannot claim to be someone else. Without AGENTKIT_PROXY_SECRET the network must enforce proxy-only access."""
    secret = os.getenv("AGENTKIT_PROXY_SECRET", "")
    return not secret or hmac.compare_digest(h.get("x-proxy-secret", ""), secret)


def authenticate(headers: dict[str, str]) -> Principal:
    """Resolve the caller from request headers. Modes (AGENTKIT_AUTH): none | jwt | proxy.
    'proxy' trusts X-Forwarded-User/-Groups set by an SSO reverse proxy (oauth2-proxy, Shibboleth SP) —
    only enable it when the app is reachable exclusively through that proxy."""
    h = {k.lower(): v for k, v in headers.items()}
    admin_key = os.getenv("AGENTKIT_ADMIN_KEY", "")
    if admin_key and hmac.compare_digest(h.get("x-api-key", ""), admin_key):
        return make_principal("admin-api-key", [], admin=True)
    mode = os.getenv("AGENTKIT_AUTH", "none")
    principal = Principal()
    if mode == "jwt" and h.get("authorization", "").lower().startswith("bearer "):
        principal = verify_jwt(h["authorization"][7:].strip())
    elif mode == "proxy" and h.get("x-forwarded-user") and _proxy_ok(h):
        groups = [g.strip() for g in h.get("x-forwarded-groups", "").split(",") if g.strip()]
        principal = make_principal(h["x-forwarded-user"], groups)
    if os.getenv("AGENTKIT_REQUIRE_LOGIN") == "1" and not principal.user:
        raise AuthError("login required")
    return principal


# Encrypted logs + retention
class SecureLog:
    """Append-only JSONL log. Rows are PII-redacted and pseudonymous; with AGENTKIT_LOG_KEY (a Fernet key)
    each row is encrypted at rest. Fernet tokens carry their timestamp, so retention needs no decryption."""

    def __init__(self, path: str | Path, key: str | None = None, retention_days: int | None = None):
        self.path = Path(path)
        self.key = key if key is not None else os.getenv("AGENTKIT_LOG_KEY", "")
        self.retention_days = retention_days or int(os.getenv("AGENTKIT_LOG_RETENTION_DAYS", "30"))
        self.lock = threading.Lock()
        self._fernet = None
        if self.key:
            from cryptography.fernet import Fernet
            self._fernet = Fernet(self.key.encode() if isinstance(self.key, str) else self.key)

    def write(self, row: dict):
        line = json.dumps(row, ensure_ascii=False)
        if self._fernet:
            line = self._fernet.encrypt(line.encode()).decode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _ts(self, line: str) -> float:
        if self._fernet:
            return self._fernet.extract_timestamp(line.encode())
        return json.loads(line).get("ts", 0)

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            out.append(json.loads(self._fernet.decrypt(line.encode()) if self._fernet else line))
        return out

    def purge(self, now: float | None = None, user: str | None = None) -> int:
        """Delete rows older than the retention window, or every row of one (pseudonymous) user
        (data-subject deletion request). Returns the number of rows removed."""
        if not self.path.exists():
            return 0
        cutoff = (now or time.time()) - self.retention_days * 86400
        keep, removed = [], 0
        with self.lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                drop = self._ts(line) < cutoff
                if user and not drop:
                    row = json.loads(self._fernet.decrypt(line.encode()) if self._fernet else line)
                    drop = row.get("user") == pseudonym(user)
                if drop:
                    removed += 1
                else:
                    keep.append(line)
            self.path.write_text("".join(x + "\n" for x in keep), encoding="utf-8")
        return removed


# Ingestion allowlist (LLM04)
def allowed_domains() -> list[str]:
    env = os.getenv("AGENTKIT_ALLOWED_DOMAINS")
    if env is not None:
        return [d.strip().lower() for d in env.split(",") if d.strip()]
    path = ROOT / "config/allowed-domains.txt"
    if path.exists():
        return [d.strip().lower() for d in path.read_text().splitlines() if d.strip() and not d.startswith("#")]
    return []


def source_allowed(url: str, domains: list[str] | None = None) -> bool:
    """Web sources must be https and on an allowlisted domain (or its subdomain). Local files (no URL) pass:
    they were placed by an operator. An empty allowlist allows everything."""
    domains = allowed_domains() if domains is None else domains
    if not domains or not re.match(r"^[a-z]+://", url):
        return True
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == d or host.endswith("." + d) for d in domains)
