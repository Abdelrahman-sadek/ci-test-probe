"""Staff dashboard data: one function per tab, each returning JSON-ready aggregates.

Groups and tabs (see docs/plans/07): Overview (today, trends) · Quality (conversations, gaps, evaluations) ·
Knowledge (sources, ocr) · Service (tickets, librarians) · Operations (costs, performance, system, security).
Questions appear only redacted; users only as pseudonyms.
"""
import datetime
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import ROOT
from .metrics import METRICS

# tab → (group, minimum role)
TABS = {
    "today": ("overview", "viewer"), "trends": ("overview", "viewer"),
    "conversations": ("quality", "viewer"), "gaps": ("quality", "viewer"), "evaluations": ("quality", "viewer"),
    "sources": ("knowledge", "staff"), "ocr": ("knowledge", "staff"),
    "tickets": ("service", "staff"), "librarians": ("service", "staff"),
    "costs": ("operations", "admin"), "performance": ("operations", "admin"), "system": ("operations", "admin"),
    "security": ("operations", "admin"),
}
RANK = {"": 0, "viewer": 1, "staff": 2, "admin": 3}


def allowed(role: str, tab: str) -> bool:
    return tab in TABS and RANK.get(role, 0) >= RANK[TABS[tab][1]]


def _day_start(now: float) -> float:
    return datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _rates(rows: list[dict]) -> dict:
    n = len(rows) or 1
    modes = Counter(r["mode"] for r in rows)
    return {"questions": len(rows), "modes": dict(modes),
            "deflection": round((modes["answer"] + modes["account"]) / n, 3) if rows else None,
            "handoff_rate": round(modes["handoff"] / n, 3) if rows else None,
            "degraded": sum(1 for r in rows if r["degraded"]), "cached": sum(1 for r in rows if r["cached"]),
            "languages": dict(Counter(r["lang"] for r in rows))}


def _satisfaction(db, since: float) -> dict:
    rows = db.db.execute("SELECT rating FROM feedback WHERE ts >= ?", (since,)).fetchall()
    up = sum(1 for (r,) in rows if r > 0)
    return {"rated": len(rows), "up": up, "satisfaction": round(up / len(rows), 3) if rows else None}


def today(chat, since: float, until: float, **_):
    db, now = chat.appdb, time.time()
    start = _day_start(now)
    rows = db.answers(start, now, limit=100000)
    return {**_rates(rows), **_satisfaction(db, start),
            "open_tickets": sum(1 for t in db.tickets() if t["status"] not in ("answered", "closed")),
            "overdue_tickets": len(db.overdue_tickets()),
            "llm": chat.llm.status() if hasattr(chat.llm, "status") else ("ok" if chat.llm.live else "offline"),
            "maintenance": chat.maintenance, "budget": chat.budget.summary() if chat.budget else None,
            "index": {"chunks": chat.index.size, "version": chat.index.version}}


def trends(chat, since: float, until: float, **_):
    days = defaultdict(list)
    for r in chat.appdb.answers(since, until, limit=200000):
        days[datetime.date.fromtimestamp(r["ts"]).isoformat()].append(r)
    fb = defaultdict(lambda: [0, 0])
    for ts, rating in chat.appdb.db.execute("SELECT ts, rating FROM feedback WHERE ts >= ? AND ts <= ?", (since, until)):
        d = fb[datetime.date.fromtimestamp(ts).isoformat()]
        d[0] += rating > 0
        d[1] += 1
    series = []
    for day in sorted(days):
        r = _rates(days[day])
        up, rated = fb.get(day, (0, 0))
        series.append({"day": day, "questions": r["questions"], "deflection": r["deflection"],
                       "handoff_rate": r["handoff_rate"], "satisfaction": round(up / rated, 3) if rated else None,
                       "arabic_share": round((r["languages"].get("ar", 0) + r["languages"].get("arabizi", 0))
                                             / max(r["questions"], 1), 3)})
    return {"series": series}


def conversations(chat, since: float, until: float, mode: str = "", agent: str = "", lang: str = "", **_):
    rows = chat.appdb.answers(since, until, limit=200, mode=mode, agent=agent, lang=lang)
    return {"rows": rows, "agents": sorted(chat.agents), "modes": ["answer", "strategy", "handoff", "refuse"]}


def gaps(chat, since: float, until: float, **_):
    from .arabic import detect_lang
    from .gaps import detect
    return detect(chat.appdb, chat.index, since=since, expand=lambda q: chat._expand(q, detect_lang(q)))


def evaluations(chat, since: float, until: float, **_):
    folder = Path(os.getenv("AGENTKIT_EVAL_DIR", "data"))
    out = {}
    for name in ("eval", "dev", "heldout", "redteam", "agents"):
        path = folder / f"{name}-report.json"
        if path.exists():
            rep = json.loads(path.read_text(encoding="utf-8"))
            failed = [{"id": r.get("id"), "question": r.get("question"),
                       "failed": [k for k, v in r.get("checks", {}).items() if not v]}
                      for r in rep.get("results", []) if not r.get("pass", True)]
            out["golden" if name == "eval" else name] = {
                k: rep.get(k) for k in ("passed", "total", "pass_rate", "recall_at_k", "mrr", "by_lang", "live", "config")
            } | {"failed": failed[:30], "updated": path.stat().st_mtime}
    critic = Counter(s.get("critic", "") for (t,) in chat.appdb.db.execute(
        "SELECT trace FROM answers WHERE ts >= ? AND trace LIKE '%critic%'", (since,))
        for s in json.loads(t) if s.get("step") == "critic")
    return {"reports": out, "critic_verdicts": {k: v for k, v in critic.items() if k}}


def sources(chat, since: float, until: float, **_):
    owners = _owners()
    verify = {c.source for c in chat.index.iter_chunks() if "[VERIFY]" in c.text}
    counts = Counter(c.source for c in chat.index.iter_chunks())
    stale_days = float(os.getenv("AGENTKIT_STALE_DAYS", "120"))
    now, rows = time.time(), []
    for origin, e in sorted(chat.index.manifest().items()):
        age = (now - e.get("ingested_at", now)) / 86400
        rows.append({"origin": origin, "url": e.get("url", ""), "status": e.get("status", ""),
                     "chunks": e.get("chunks", counts.get(e.get("url", origin), 0)), "age_days": round(age, 1),
                     "stale": age > stale_days, "verify": e.get("url", origin) in verify or origin in verify,
                     "owner": next((o for k, o in owners.items() if k in origin), ""),
                     "low_confidence_pages": e.get("low_confidence_pages", [])})
    return {"rows": rows, "pending_review": sum(1 for r in rows if r["status"] == "pending"),
            "notices": chat.appdb.notices(include_expired=True)}


def _owners() -> dict:
    """Content owners by source keyword (RUNBOOK defaults until AUC assigns them)."""
    return {"borrow": "Access services", "hours": "Access services", "rbscl": "RBSCL research services",
            "research-help": "Reference and instruction", "knowledge-fountain": "Scholarly communication",
            "src-library": "Social Research Center"}


def ocr(chat, since: float, until: float, **_):
    from .ocr import LOW_CONFIDENCE
    buckets, methods, pending = Counter(), Counter(), []
    for c in chat.index.iter_chunks():
        methods[c.method] += 1
        if c.method.startswith("ocr"):
            buckets[f"{int(c.confidence * 10) / 10:.1f}"] += 1
            if c.confidence < LOW_CONFIDENCE:
                pending.append({"source": c.source, "page": c.page, "confidence": c.confidence})
    seen, queue = set(), []
    for p in pending:
        if (p["source"], p["page"]) not in seen:
            seen.add((p["source"], p["page"]))
            queue.append(p)
    return {"methods": dict(methods), "confidence_histogram": dict(sorted(buckets.items())),
            "correction_queue": queue[:200], "threshold": LOW_CONFIDENCE}


def tickets(chat, since: float, until: float, **_):
    db, now = chat.appdb, time.time()
    sla = float(os.getenv("AGENTKIT_TICKET_SLA_HOURS", "48"))
    rows = [{"id": t["id"], "kind": t["kind"], "status": t["status"], "routed_to": t["routed_to"],
             "age_hours": round((now - t["ts"]) / 3600, 1), "subject": t.get("subject", ""),
             "question": t.get("question", ""), "overdue": t["status"] in ("queued", "sent") and now - t["ts"] > sla * 3600,
             "delivery_failures": t.get("delivery_failures", [])} for t in db.tickets() if t["ts"] >= since]
    return {"rows": rows, "workload": db.workload(), "sla_hours": sla}


def librarians(chat, since: float, until: float, **_):
    data = json.loads((ROOT / "knowledge/auc-library/librarians.json").read_text(encoding="utf-8"))
    load = chat.appdb.workload()
    rota = json.loads((ROOT / "knowledge/auc-library/signoff.json").read_text(encoding="utf-8")).get("staff_rota", {})
    return {"queues": [{**{k: v for k, v in s.items() if k != "keywords"}, "keywords": len(s.get("keywords", [])),
                        "workload": load.get(s["subject"], {})} for s in [data["default"], *data["subjects"]]],
            "rota": rota}


def costs(chat, since: float, until: float, **_):
    db = chat.appdb
    return {"budget": chat.budget.summary() if chat.budget else None,
            "by_plugin": db.usage_breakdown(since, "plugin"), "by_purpose": db.usage_breakdown(since, "purpose"),
            "by_agent": db.usage_breakdown(since, "agent"), "by_model": db.usage_breakdown(since, "model"),
            "daily": db.daily_spend(max(1, int((until - since) / 86400) + 1)),
            "answered": sum(1 for r in db.answers(since, until, limit=200000) if r["mode"] == "answer"),
            "cache_hits": METRICS.value("agentkit_cache_hits_total"),
            "semantic_hits": METRICS.value("agentkit_semantic_cache_hits_total"),
            "saved_usd_estimate": round(METRICS.value("agentkit_cache_saved_usd_total"), 4)}


def performance(chat, since: float, until: float, **_):
    steps = defaultdict(list)
    totals = []
    for r in chat.appdb.answers(since, until, limit=5000):
        totals.append(r["ms"])
        for s in r["trace"]:
            steps[s["step"]].append(s.get("ms", 0))

    def pct(v, q):
        v = sorted(v)
        return round(v[min(len(v) - 1, int(q * len(v)))], 1) if v else None
    return {"total_ms": {"p50": pct(totals, .5), "p95": pct(totals, .95), "n": len(totals)},
            "steps": {k: {"p50": pct(v, .5), "p95": pct(v, .95), "n": len(v), "mean": round(statistics.mean(v), 1)}
                      for k, v in steps.items()},
            "counters": {k: METRICS.value(k) for k in ("agentkit_rewrites_total", "agentkit_grade_dropped_total",
                                                       "agentkit_cache_hits_total", "agentkit_llm_failures_total",
                                                       "agentkit_degraded_total", "agentkit_semantic_cache_hits_total")}}


def system(chat, since: float, until: float, index_path: str = "", **_):
    from .preflight import check
    snaps = Path(index_path + ".snapshots") if index_path else None
    return {"preflight": check(chat.index), "maintenance": chat.maintenance,
            "snapshots": sorted(p.name for p in snaps.iterdir())[-10:] if snaps and snaps.exists() else [],
            "connectors": {"libcal": bool(chat.libcal), "primo": bool(chat.catalog), "alma": bool(chat.account),
                           "libanswers": bool(os.getenv("AGENTKIT_LIBANSWERS_URL")),
                           "smtp": bool(os.getenv("AGENTKIT_SMTP_HOST"))},
            "index": {"chunks": chat.index.size, "version": chat.index.version, "path": index_path}}


def security(chat, since: float, until: float, **_):
    rows = chat.appdb.answers(since, until, limit=200000)
    quarantined = sum(1 for c in chat.index.iter_chunks() if "injection" in c.flags)
    acts = chat.appdb.actions(200)
    return {"blocked_by_guard": dict(Counter(r["guard"] for r in rows if r["guard"])),
            "rate_limited": METRICS.value("agentkit_rate_limited_total"), "quarantined_chunks": quarantined,
            "data_requests": [a for a in acts if a["action"].startswith("data-")],
            "admin_actions": [a for a in acts if not a["action"].startswith("data-")][:100]}


FUNCS = {name: globals()[name] for name in TABS}


def tab(chat, name: str, since: float | None = None, until: float | None = None, **filters) -> dict:
    until = until or time.time()
    since = since if since is not None else until - 30 * 86400
    return {"tab": name, "group": TABS[name][0], "since": since, "until": until,
            "data": FUNCS[name](chat, since, until, **filters)}
