"""Go/no-go checks before real users see the assistant (`agentkit preflight`, and `serve` with AGENTKIT_PILOT=1).

Blocking: unverified facts that retrieval could return, missing live model, open authentication, spoofable
proxy identity, unencrypted logs, weak admin key. Warnings: things a pilot can run without but should fix.
"""
import json
import os
from pathlib import Path

from . import ROOT

KNOWLEDGE_FILES = ("librarians.json", "referrals.json", "related-topics.json")


def check(index) -> dict:
    blocking, warnings = [], []
    unverified = sorted({c.source for c in index.iter_chunks() if "[VERIFY]" in c.text and not c.blocked})
    if unverified:
        blocking.append(f"{len(unverified)} indexed source(s) contain [VERIFY] facts: {', '.join(unverified[:5])}")
    for name in KNOWLEDGE_FILES:
        path = ROOT / "knowledge/auc-library" / name
        if path.exists() and "[VERIFY]" in path.read_text(encoding="utf-8"):
            blocking.append(f"knowledge/auc-library/{name} still has [VERIFY] entries (contacts, URLs)")
    signoff = ROOT / "knowledge/auc-library/signoff.json"
    so = json.loads(signoff.read_text(encoding="utf-8")) if signoff.exists() else {}
    if not (so.get("approved_by") and so.get("date")):
        blocking.append("no AUC sign-off on corpus scope: fill approved_by and date in knowledge/auc-library/signoff.json")
    if index.size == 0:
        blocking.append("the index is empty")
    blocking += _live_evals(index, os.getenv("AGENTKIT_EVAL_DIR", "data"))
    if not os.getenv("ANTHROPIC_API_KEY"):
        blocking.append("ANTHROPIC_API_KEY is not set: answers would come from the offline extractive stand-in")
    auth = os.getenv("AGENTKIT_AUTH", "none")
    if auth == "none":
        blocking.append("AGENTKIT_AUTH=none: no sign-in, so access levels and data rights cannot work")
    if auth == "proxy" and not os.getenv("AGENTKIT_PROXY_SECRET"):
        blocking.append("AGENTKIT_AUTH=proxy without AGENTKIT_PROXY_SECRET: identity headers can be spoofed "
                        "by anyone who reaches the app without the proxy")
    if auth == "jwt" and os.getenv("AGENTKIT_JWT_SECRET") and not os.getenv("AGENTKIT_JWT_JWKS"):
        warnings.append("JWT uses a shared HS256 secret; prefer the IdP's keys via AGENTKIT_JWT_JWKS")
    if not os.getenv("AGENTKIT_LOG_KEY"):
        blocking.append("AGENTKIT_LOG_KEY is not set: question logs would be stored unencrypted")
    if os.getenv("AGENTKIT_LOG_SALT", "agentkit-dev-salt") == "agentkit-dev-salt":
        blocking.append("AGENTKIT_LOG_SALT is the development default: pseudonyms would be guessable")
    if 0 < len(os.getenv("AGENTKIT_ADMIN_KEY", "")) < 32:
        blocking.append("AGENTKIT_ADMIN_KEY is shorter than 32 characters")
    if not os.getenv("AGENTKIT_DAILY_BUDGET_USD"):
        warnings.append("no AGENTKIT_DAILY_BUDGET_USD: model spend is uncapped")
    if not (os.getenv("AGENTKIT_LIBANSWERS_URL") or os.getenv("AGENTKIT_SMTP_HOST")):
        warnings.append("handoffs only reach the staff page queue (no LibAnswers or SMTP configured)")
    if not os.getenv("AGENTKIT_ESCALATION_EMAIL"):
        warnings.append("no AGENTKIT_ESCALATION_EMAIL: overdue handoffs are marked but nobody is emailed")
    if not Path(os.getenv("AGENTKIT_APP_DB", "data/app.db")).parent.exists():
        warnings.append("the AppDB directory does not exist yet")
    return {"ok": not blocking, "blocking": blocking, "warnings": warnings}


def _live_evals(index, folder: str) -> list[str]:
    """The pilot needs evals run against the live model on the current index, not the offline stand-in."""
    need = {"eval": float(os.getenv("AGENTKIT_PILOT_MIN_GOLDEN", "0.95")),
            "heldout": float(os.getenv("AGENTKIT_PILOT_MIN_HELDOUT", "0.8"))}
    problems = []
    for name, floor in need.items():
        path = Path(folder) / f"{name}-report.json"
        rep = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        label = "golden" if name == "eval" else name
        if not rep:
            problems.append(f"no {label} eval report ({path}); run `agentkit eval --set {label}` with the live model")
        elif not rep.get("live"):
            problems.append(f"the {label} eval ran on the offline stand-in; re-run it with ANTHROPIC_API_KEY set")
        elif rep.get("index_version") != index.version:
            problems.append(f"the {label} eval is older than the current index; re-run it")
        elif rep["pass_rate"] < floor:
            problems.append(f"{label} eval pass rate {rep['pass_rate']} is below {floor}")
    return problems
