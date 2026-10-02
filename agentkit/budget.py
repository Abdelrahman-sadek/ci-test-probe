"""Model spend against budget, from the persistent `llm_usage` table, so a restart cannot reset the cap.

* AGENTKIT_DAILY_BUDGET_USD / AGENTKIT_MONTHLY_BUDGET_USD: caps. At 100 % answers switch to search results only.
* AGENTKIT_BUDGET_ALERTS (default 50,80,95): each threshold alerts once per day/month by email and/or webhook.
* AGENTKIT_BUDGET_BRAKE (default 0.8): above this share, optional calls (grade, rewrite, critic) stop first.
"""
import datetime
import json
import logging
import os
import time

log = logging.getLogger("agentkit.budget")


def _start_of(period: str, now: float) -> float:
    d = datetime.datetime.fromtimestamp(now)
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        d = d.replace(day=1)
    return d.timestamp()


class BudgetMonitor:
    def __init__(self, appdb, notify=None, daily: float | None = None, monthly: float | None = None):
        self.appdb = appdb
        env = lambda k: float(os.environ[k]) if os.getenv(k) else None  # noqa: E731
        self.caps = {"day": daily if daily is not None else env("AGENTKIT_DAILY_BUDGET_USD"),
                     "month": monthly if monthly is not None else env("AGENTKIT_MONTHLY_BUDGET_USD")}
        self.thresholds = sorted(int(x) for x in os.getenv("AGENTKIT_BUDGET_ALERTS", "50,80,95").split(",") if x)
        self.brake_at = float(os.getenv("AGENTKIT_BUDGET_BRAKE", "0.8"))
        self.notify = notify or default_notify
        self.pending = 0.0  # estimated cost of calls in flight: reserved before a call, released after
        import threading
        self._lock = threading.Lock()

    def estimate(self) -> float:
        env = os.getenv("AGENTKIT_EST_CALL_USD")
        if env:
            return float(env)
        row = self.appdb.db.execute("SELECT AVG(cost) FROM (SELECT cost FROM llm_usage WHERE cost > 0 "
                                    "ORDER BY id DESC LIMIT 50)").fetchone()
        return row[0] or 0.0  # no history yet: the first call may overshoot by itself, never more

    def reserve(self) -> float | None:
        """Reserve one call's estimated cost; None when it would cross a cap. Parallel requests therefore
        cannot overshoot the cap by more than the estimate error."""
        est = self.estimate()
        with self._lock:
            for period, cap in self.caps.items():
                if cap and self.spend(period) + self.pending + est > cap:
                    return None
            self.pending += est
        return est

    def release(self, est: float):
        with self._lock:
            self.pending = max(0.0, self.pending - est)

    def spend(self, period: str, now: float | None = None) -> float:
        return self.appdb.spend(_start_of(period, now or time.time()))

    def fraction(self, now: float | None = None) -> float:
        """Highest share of any configured cap used so far (0 when no cap is set)."""
        return max(((self.spend(p, now) + self.pending) / cap for p, cap in self.caps.items() if cap), default=0.0)

    def over(self) -> bool:
        """At the cap, or the next call (estimated) would cross it."""
        est = self.estimate()
        return self.fraction() >= 1.0 or any(cap and self.spend(p) + self.pending + est > cap
                                             for p, cap in self.caps.items())

    def brake(self) -> bool:
        return self.fraction() >= self.brake_at

    def check(self, now: float | None = None) -> list[str]:
        """Fire each newly crossed threshold once per period. Returns the alert messages sent."""
        now = now or time.time()
        sent = []
        for period, cap in self.caps.items():
            if not cap:
                continue
            spent = self.spend(period, now)
            key = f"{period}:{datetime.date.fromtimestamp(now).isoformat()[:10 if period == 'day' else 7]}"
            for t in self.thresholds:
                if spent >= cap * t / 100 and self.appdb.alert_once(key, t):
                    msg = (f"AUC Library assistant: {t}% of the {period}ly model budget used "
                           f"(${spent:.2f} of ${cap:.2f}).")
                    if t >= self.brake_at * 100:
                        msg += " Optional model calls (grading, rewriting, critic) are paused."
                    self.notify(msg)
                    sent.append(msg)
        return sent

    def summary(self, now: float | None = None) -> dict:
        now = now or time.time()
        days_in_month = (datetime.date.fromtimestamp(now).replace(day=28) + datetime.timedelta(days=4)).replace(
            day=1) - datetime.timedelta(days=1)
        week = self.appdb.spend(now - 7 * 86400) / 7
        return {"today": round(self.spend("day", now), 4), "month": round(self.spend("month", now), 4),
                "daily_cap": self.caps["day"], "monthly_cap": self.caps["month"], "fraction": round(self.fraction(now), 3),
                "brake": self.fraction(now) >= self.brake_at, "over": self.fraction(now) >= 1.0,
                "forecast_month": round(self.spend("month", now) + week * (days_in_month.day -
                                                                           datetime.date.fromtimestamp(now).day), 2),
                "thresholds": self.thresholds}


def default_notify(message: str):
    """Email the escalation mailbox and/or POST to AGENTKIT_ALERT_WEBHOOK (https only, e.g. Teams/Slack)."""
    log.warning(message)
    hook = os.getenv("AGENTKIT_ALERT_WEBHOOK", "")
    if hook.startswith("https://"):
        try:
            from .connectors import _https_json
            _https_json(hook, {"text": message}, {})
        except Exception as e:  # noqa: BLE001
            log.error("budget webhook failed: %s", type(e).__name__)
    to, host = os.getenv("AGENTKIT_ESCALATION_EMAIL", ""), os.getenv("AGENTKIT_SMTP_HOST", "")
    if to and host:
        try:
            import smtplib
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["Subject"], msg["To"] = "[Library assistant] budget alert", to
            msg["From"] = os.getenv("AGENTKIT_SMTP_FROM", "library-assistant@localhost")
            msg.set_content(json.dumps({"alert": message}))
            with smtplib.SMTP(host, int(os.getenv("AGENTKIT_SMTP_PORT", "587")), timeout=10) as s:
                if os.getenv("AGENTKIT_SMTP_STARTTLS", "1") == "1":
                    s.starttls()
                if os.getenv("AGENTKIT_SMTP_USER"):
                    s.login(os.environ["AGENTKIT_SMTP_USER"], os.getenv("AGENTKIT_SMTP_PASS", ""))
                s.send_message(msg)
        except Exception as e:  # noqa: BLE001
            log.error("budget email failed: %s", type(e).__name__)


def attach(llm, appdb, notify=None) -> BudgetMonitor:
    """Persist every model call to AppDB, check alert thresholds after each, and give the LLM wrapper the
    monitor so the cap and the soft brake read persistent spend. Replaces any earlier accounting sink."""
    from .metrics import METRICS
    monitor = BudgetMonitor(appdb, notify)

    def sink(model, tokens, cost, labels):
        appdb.add_usage(model, tokens, cost, labels)
        if cost:
            monitor.check()
    sink.agentkit_accounting = True
    METRICS.sinks[:] = [s for s in METRICS.sinks if not getattr(s, "agentkit_accounting", False)] + [sink]
    if hasattr(llm, "monitor"):
        llm.monitor = monitor
    return monitor
