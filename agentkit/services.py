"""Human services behind the chat: subject-librarian routing, real handoff tickets (LibAnswers, email or a
local queue) routed by opening hours, and special-collections request intake."""
import json
import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

from . import ROOT
from .arabic import normalize
from .security import redact

LIBRARIANS = ROOT / "knowledge/auc-library/librarians.json"


def librarians() -> dict:
    path = Path(os.getenv("AGENTKIT_LIBRARIANS", LIBRARIANS))
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"default": {}, "subjects": []}


def match_librarian(text: str) -> dict:
    """Pick the subject specialist whose keywords best match the question (EN/AR); else the reference desk."""
    cfg, norm = librarians(), normalize(text)
    best, score = cfg.get("default", {}), 0
    for s in cfg.get("subjects", []):
        n = sum(1 for k in s.get("keywords", []) if re.search(rf"\b{re.escape(normalize(k))}", norm))
        if n > score:
            best, score = s, n
    return best


def transcript_summary(history: list[dict], question: str, limit: int = 1500) -> str:
    turns = [f"{h.get('role', 'user')}: {h.get('content', '')}" for h in (history or [])[-6:]]
    return redact("\n".join([*turns, f"user: {question}"]))[-limit:]


class Handoff:
    """Deliver a handoff. Backends (first configured wins): LibAnswers ticket API → email (SMTP) → local queue.
    The ticket is always recorded in AppDB so staff can see it on the admin page."""

    def __init__(self, appdb, libcal=None, post=None, smtp=None):
        self.appdb, self.libcal = appdb, libcal
        self.post = post            # injectable for tests: post(url, data, headers)
        self.smtp = smtp            # injectable SMTP factory

    def _libanswers(self, payload: dict, routed: dict) -> str | None:
        base, token = os.getenv("AGENTKIT_LIBANSWERS_URL", ""), os.getenv("AGENTKIT_LIBANSWERS_TOKEN", "")
        queue = routed.get("libanswers_queue") or os.getenv("AGENTKIT_LIBANSWERS_QUEUE", "")
        if not (base and token and queue):
            return None
        from .connectors import _https_json
        post = self.post or (lambda url, data, headers: _https_json(url, data, headers))
        res = post(f"{base.rstrip('/')}/api/1.1/ticket/create",  # field names per LibAnswers API [VERIFY]
                   {"quid": queue, "pquestion": payload["question"][:150], "pdetails": payload["summary"],
                    "pemail": payload.get("email", ""), "pname": payload.get("name", "")},
                   {"Authorization": f"Bearer {token}"})
        return f"libanswers:{res.get('ticketUrl') or res.get('id') or 'created'}"

    def _email(self, payload: dict, routed: dict) -> str | None:
        host, to = os.getenv("AGENTKIT_SMTP_HOST", ""), routed.get("email") or os.getenv("AGENTKIT_HANDOFF_EMAIL", "")
        if not (host and to):
            return None
        msg = EmailMessage()
        msg["Subject"] = f"[Library assistant] {payload['kind']}: {payload['question'][:80]}"
        msg["From"] = os.getenv("AGENTKIT_SMTP_FROM", "library-assistant@localhost")
        msg["To"] = to
        if payload.get("email"):
            msg["Reply-To"] = payload["email"]
        msg.set_content(json.dumps(payload, ensure_ascii=False, indent=2))
        factory = self.smtp or (lambda: smtplib.SMTP(host, int(os.getenv("AGENTKIT_SMTP_PORT", "587")), timeout=10))
        with factory() as s:
            if os.getenv("AGENTKIT_SMTP_STARTTLS", "1") == "1":
                s.starttls()
            if os.getenv("AGENTKIT_SMTP_USER"):
                s.login(os.environ["AGENTKIT_SMTP_USER"], os.getenv("AGENTKIT_SMTP_PASS", ""))
            s.send_message(msg)
        return f"email:{to}"

    def create(self, kind: str, question: str, history: list[dict] | None = None, email: str = "",
               name: str = "", consent: bool = False, extra: dict | None = None, user: str = "") -> dict:
        if email and not consent:
            raise ValueError("consent is required to share your email with library staff")
        routed = match_librarian(" ".join([question, *(h.get("content", "") for h in history or [])]))
        payload = {"kind": kind, "question": redact(question[:500]), "summary": transcript_summary(history or [], question),
                   "email": email if consent else "", "name": name[:100] if consent else "",
                   "subject": routed.get("subject", "Reference"), **(extra or {})}
        delivered, failures = None, []
        for backend in (self._libanswers, self._email):  # a failing backend never drops the request silently
            try:
                delivered = backend(payload, routed)
            except Exception as e:  # noqa: BLE001
                failures.append(f"{backend.__name__.strip('_')}: {type(e).__name__}")
                from .metrics import METRICS
                METRICS.inc("agentkit_handoff_failures_total", backend=backend.__name__.strip("_"))
            if delivered:
                break
        delivered = delivered or "queue"
        if failures:
            payload["delivery_failures"] = failures
        tid = self.appdb.add_ticket(kind, payload, routed.get("subject", "Reference"), user,
                                    status="sent" if delivered != "queue" else "queued")
        open_now = self.libcal.is_open_now() if self.libcal else None
        when = {True: "The library is open now, so a librarian should reply soon.",
                False: "The library is closed now; a librarian will reply when it reopens.",
                None: "A librarian will reply during opening hours."}[open_now]
        if failures and delivered == "queue":
            when = ("We couldn't reach the librarians' inbox just now, but your question is saved (reference above) "
                    "and staff will see it on their dashboard. " + when)
        return {"ticket": tid, "delivered_via": delivered.split(":")[0], "routed_to": routed.get("subject", "Reference"),
                "contact": routed.get("contact", ""), "booking_url": routed.get("booking_url", ""), "message": when}


def escalate_overdue(appdb, handoff: "Handoff | None" = None) -> list[str]:
    """Mark tickets past the SLA (AGENTKIT_TICKET_SLA_HOURS, default 48) as escalated and notify the
    escalation mailbox (AGENTKIT_ESCALATION_EMAIL) when email is configured. Run from cron: `agentkit tickets-check`."""
    done = []
    for t in appdb.overdue_tickets():
        appdb.set_ticket_status(t["id"], "escalated")
        done.append(t["id"])
    to = os.getenv("AGENTKIT_ESCALATION_EMAIL", "")
    if done and to and handoff is not None and os.getenv("AGENTKIT_SMTP_HOST"):
        try:
            handoff._email({"kind": "escalation", "question": f"{len(done)} overdue ticket(s)",
                            "summary": ", ".join(done)}, {"email": to})
        except Exception:  # noqa: BLE001 — escalation status is recorded even if the email fails
            pass
    return done
