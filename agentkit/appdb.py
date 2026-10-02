"""Application state in one SQLite file (default data/app.db): feedback, unanswered questions, saved
conversations and searches, pinned notices, librarian handoff tickets and rare-book requests.

Free-text fields are PII-redacted and, when AGENTKIT_LOG_KEY is set, Fernet-encrypted at rest. Users are
stored only as pseudonyms. `purge()` applies the retention window (AGENTKIT_LOG_RETENTION_DAYS).
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .security import pseudonym, redact

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, ts REAL, user TEXT, answer_id TEXT, rating INTEGER,
                                    reason TEXT, question TEXT, mode TEXT, lang TEXT);
CREATE TABLE IF NOT EXISTS unanswered(id TEXT PRIMARY KEY, ts REAL, user TEXT, question TEXT, mode TEXT,
                                      lang TEXT, agent TEXT);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, ts REAL, user TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY, conversation TEXT, ts REAL, role TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS saved(id TEXT PRIMARY KEY, ts REAL, user TEXT, question TEXT, answer TEXT);
CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY, ts REAL, title TEXT, body TEXT, url TEXT,
                                   valid_from TEXT, valid_to TEXT, priority TEXT, lang TEXT);
CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, ts REAL, user TEXT, kind TEXT, status TEXT,
                                   routed_to TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS turns_conv ON turns(conversation);
CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY, ts REAL, user TEXT, question TEXT, mode TEXT, agent TEXT,
                                   lang TEXT, cached INTEGER, degraded TEXT, guard TEXT, sources TEXT, trace TEXT,
                                   ms REAL);
CREATE INDEX IF NOT EXISTS answers_ts ON answers(ts);
CREATE TABLE IF NOT EXISTS llm_usage(id INTEGER PRIMARY KEY, ts REAL, model TEXT, plugin TEXT, agent TEXT,
                                     purpose TEXT, input INTEGER, output INTEGER, cache_read INTEGER,
                                     cache_write INTEGER, cost REAL);
CREATE INDEX IF NOT EXISTS usage_ts ON llm_usage(ts);
CREATE TABLE IF NOT EXISTS budget_alerts(period TEXT, threshold INTEGER, ts REAL, PRIMARY KEY(period, threshold));
CREATE TABLE IF NOT EXISTS admin_actions(id INTEGER PRIMARY KEY, ts REAL, actor TEXT, action TEXT, detail TEXT);
"""
RETENTION_TABLES = ("feedback", "unanswered", "conversations", "saved", "tickets", "answers")


class AppDB:
    def __init__(self, path: str | Path = "", key: str | None = None):
        self.path = str(path or os.getenv("AGENTKIT_APP_DB", "data/app.db"))
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        key = key if key is not None else os.getenv("AGENTKIT_LOG_KEY", "")
        self._f = None
        if key:
            from cryptography.fernet import Fernet
            self._f = Fernet(key.encode() if isinstance(key, str) else key)

    # Encryption helpers
    def _enc(self, text: str) -> str:
        return self._f.encrypt(text.encode()).decode() if self._f else text

    def _dec(self, text: str) -> str:
        return self._f.decrypt(text.encode()).decode() if self._f and text else text

    def _exec(self, sql: str, args=()):
        with self.lock, self.db:
            return self.db.execute(sql, args)

    # 1. feedback + unanswered questions
    def add_feedback(self, answer_id: str, rating: int, reason: str = "", question: str = "", mode: str = "",
                     lang: str = "", user: str = "") -> str:
        fid = uuid.uuid4().hex[:12]
        self._exec("INSERT INTO feedback VALUES (?,?,?,?,?,?,?,?,?)",
                   (fid, time.time(), pseudonym(user), answer_id, 1 if rating > 0 else -1,
                    self._enc(redact(reason[:500])), self._enc(redact(question[:1000])), mode, lang))
        return fid

    def add_unanswered(self, question: str, mode: str, lang: str, agent: str, user: str = ""):
        self._exec("INSERT INTO unanswered VALUES (?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex[:12], time.time(), pseudonym(user), self._enc(redact(question[:1000])),
                    mode, lang, agent))

    def feedback_report(self) -> dict:
        rows = self.db.execute("SELECT rating, reason, question, mode, lang FROM feedback").fetchall()
        un = self.db.execute("SELECT question, mode, lang, agent FROM unanswered").fetchall()
        down = [{"question": self._dec(q), "reason": self._dec(r), "mode": m, "lang": lang}
                for rating, r, q, m, lang in rows if rating < 0]
        return {"ratings": len(rows), "up": sum(1 for r in rows if r[0] > 0), "down": len(down),
                "thumbs_down": down,
                "unanswered": [{"question": self._dec(q), "mode": m, "lang": lang, "agent": a} for q, m, lang, a in un]}

    def eval_candidates(self) -> list[dict]:
        """Unanswered and thumbs-down questions, deduplicated — candidates for the golden test set."""
        rep, seen, out = self.feedback_report(), set(), []
        for row in rep["unanswered"] + rep["thumbs_down"]:
            q = row["question"].strip()
            if q and q.lower() not in seen:
                seen.add(q.lower())
                out.append({"question": q, "lang": row.get("lang", ""), "seen_as": row.get("mode", "")})
        return out

    # 2. conversations + saved searches (signed-in users only)
    def add_turn(self, user: str, conversation: str | None, role: str, content: str) -> str:
        conversation = conversation or uuid.uuid4().hex[:12]
        owner = self.db.execute("SELECT user FROM conversations WHERE id=?", (conversation,)).fetchone()
        if owner is None:
            title = redact(content)[:80] if role == "user" else "Conversation"
            self._exec("INSERT INTO conversations VALUES (?,?,?,?)",
                       (conversation, time.time(), pseudonym(user), self._enc(title)))
        elif owner[0] != pseudonym(user):
            raise PermissionError("conversation belongs to another user")
        self._exec("INSERT INTO turns(conversation, ts, role, content) VALUES (?,?,?,?)",
                   (conversation, time.time(), role, self._enc(redact(content[:4000]))))
        return conversation

    def conversations(self, user: str) -> list[dict]:
        rows = self.db.execute("SELECT id, ts, title FROM conversations WHERE user=? ORDER BY ts DESC LIMIT 50",
                               (pseudonym(user),)).fetchall()
        return [{"id": i, "ts": ts, "title": self._dec(t)} for i, ts, t in rows]

    def conversation(self, user: str, conversation: str) -> list[dict]:
        owner = self.db.execute("SELECT user FROM conversations WHERE id=?", (conversation,)).fetchone()
        if not owner or owner[0] != pseudonym(user):
            return []
        rows = self.db.execute("SELECT role, content FROM turns WHERE conversation=? ORDER BY id", (conversation,))
        return [{"role": r, "content": self._dec(c)} for r, c in rows]

    def save_search(self, user: str, question: str, answer: str = "") -> str:
        sid = uuid.uuid4().hex[:12]
        self._exec("INSERT INTO saved VALUES (?,?,?,?,?)", (sid, time.time(), pseudonym(user),
                                                            self._enc(redact(question[:1000])), self._enc(redact(answer[:4000]))))
        return sid

    def saved(self, user: str) -> list[dict]:
        rows = self.db.execute("SELECT id, ts, question, answer FROM saved WHERE user=? ORDER BY ts DESC LIMIT 100",
                               (pseudonym(user),)).fetchall()
        return [{"id": i, "ts": ts, "question": self._dec(q), "answer": self._dec(a)} for i, ts, q, a in rows]

    def delete_saved(self, user: str, sid: str) -> bool:
        return self._exec("DELETE FROM saved WHERE id=? AND user=?", (sid, pseudonym(user))).rowcount > 0

    # 4. pinned notices (closures, exam hours, outages)
    def add_notice(self, title: str, body: str, url: str = "", valid_from: str = "", valid_to: str = "",
                   priority: str = "urgent", lang: str = "en") -> str:
        nid = uuid.uuid4().hex[:12]
        self._exec("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?)",
                   (nid, time.time(), title[:200], body[:2000], url[:500], valid_from, valid_to, priority, lang))
        return nid

    def notices(self, today: str | None = None, include_expired: bool = False) -> list[dict]:
        import datetime
        today = today or datetime.date.today().isoformat()
        rows = self.db.execute("SELECT id, title, body, url, valid_from, valid_to, priority, lang FROM notices "
                               "ORDER BY ts DESC").fetchall()
        out = []
        for i, t, b, u, vf, vt, p, lang in rows:
            if include_expired or ((not vf or vf <= today) and (not vt or today <= vt)):
                out.append({"id": i, "title": t, "body": b, "url": u, "valid_from": vf, "valid_to": vt,
                            "priority": p, "lang": lang})
        return out

    def delete_notice(self, nid: str) -> bool:
        return self._exec("DELETE FROM notices WHERE id=?", (nid,)).rowcount > 0

    # 9 / 12. handoff tickets and special-collections requests
    def add_ticket(self, kind: str, payload: dict, routed_to: str, user: str = "", status: str = "queued") -> str:
        tid = uuid.uuid4().hex[:10].upper()
        self._exec("INSERT INTO tickets VALUES (?,?,?,?,?,?,?)",
                   (tid, time.time(), pseudonym(user), kind, status, routed_to,
                    self._enc(json.dumps(payload, ensure_ascii=False))))
        return tid

    def tickets(self, kind: str | None = None) -> list[dict]:
        if kind:
            rows = self.db.execute("SELECT id, ts, kind, status, routed_to, payload FROM tickets WHERE kind=? "
                                   "ORDER BY ts DESC LIMIT 200", (kind,)).fetchall()
        else:
            rows = self.db.execute("SELECT id, ts, kind, status, routed_to, payload FROM tickets "
                                   "ORDER BY ts DESC LIMIT 200").fetchall()
        return [{"id": i, "ts": ts, "kind": k, "status": s, "routed_to": r, **json.loads(self._dec(p))}
                for i, ts, k, s, r, p in rows]

    def set_ticket_status(self, tid: str, status: str) -> bool:
        return self._exec("UPDATE tickets SET status=? WHERE id=?", (status, tid)).rowcount > 0

    # Staff operations: SLA and workload
    def overdue_tickets(self, hours: float | None = None, now: float | None = None) -> list[dict]:
        hours = hours if hours is not None else float(os.getenv("AGENTKIT_TICKET_SLA_HOURS", "48"))
        cutoff = (now or time.time()) - hours * 3600
        return [t for t in self.tickets() if t["status"] in ("queued", "sent") and t["ts"] < cutoff]

    def workload(self, now: float | None = None) -> dict:
        """Queue depth and age per subject queue, so staffing can follow demand."""
        now = now or time.time()
        out = {}
        for t in self.tickets():
            q = out.setdefault(t["routed_to"], {"open": 0, "closed": 0, "oldest_open_hours": 0.0, "kinds": {}})
            q["kinds"][t["kind"]] = q["kinds"].get(t["kind"], 0) + 1
            if t["status"] in ("answered", "closed"):
                q["closed"] += 1
            else:
                q["open"] += 1
                q["oldest_open_hours"] = max(q["oldest_open_hours"], round((now - t["ts"]) / 3600, 1))
        return out

    # Data-subject rights (Egypt PDPL 151/2020: access and erasure)
    def export_user(self, user: str) -> dict:
        pid = pseudonym(user)
        convs = [{"id": c["id"], "title": c["title"], "turns": self.conversation(user, c["id"])}
                 for c in self.conversations(user)]
        fb = self.db.execute("SELECT ts, rating, reason, question FROM feedback WHERE user=?", (pid,)).fetchall()
        tix = self._tickets_of(pid)
        return {"conversations": convs, "saved": self.saved(user),
                "feedback": [{"ts": ts, "rating": r, "reason": self._dec(re_), "question": self._dec(q)} for ts, r, re_, q in fb],
                "tickets": tix}

    def _tickets_of(self, pid: str) -> list[dict]:
        rows = self.db.execute("SELECT id, ts, kind, status, routed_to, payload FROM tickets WHERE user=?", (pid,))
        return [{"id": i, "ts": ts, "kind": k, "status": s_, "routed_to": r, **json.loads(self._dec(p))}
                for i, ts, k, s_, r, p in rows]

    def delete_user(self, user: str) -> int:
        pid, removed = pseudonym(user), 0
        with self.lock, self.db:
            for (cid,) in self.db.execute("SELECT id FROM conversations WHERE user=?", (pid,)).fetchall():
                removed += self.db.execute("DELETE FROM turns WHERE conversation=?", (cid,)).rowcount
            for table in ("conversations", "saved", "feedback", "unanswered", "tickets", "answers"):
                removed += self.db.execute(f"DELETE FROM {table} WHERE user=?", (pid,)).rowcount  # nosec B608 — fixed names
        return removed

    # Answers, model usage, budget alerts, staff audit trail (dashboard data)
    def add_answer(self, question: str, ans: dict, user: str = "", cached: bool = False, ms: float = 0.0):
        self._exec("INSERT OR REPLACE INTO answers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (ans["id"], time.time(), pseudonym(user) if user else "", self._enc(redact(question)),
                    ans["mode"], ans["agent"], ans["lang"], int(cached), ans.get("degraded", ""), ans.get("guard", ""),
                    json.dumps([s["title"] for s in ans.get("sources", [])], ensure_ascii=False),
                    json.dumps(ans.get("trace", [])), round(ms, 1)))

    def answers(self, since: float = 0, until: float | None = None, limit: int = 200, mode: str = "",
                agent: str = "", lang: str = "") -> list[dict]:
        rows = self.db.execute("SELECT id, ts, question, mode, agent, lang, cached, degraded, guard, sources, trace, ms "
                               "FROM answers WHERE ts >= ? AND ts <= ? AND (?='' OR mode=?) AND (?='' OR agent=?) "
                               "AND (?='' OR lang=?) ORDER BY ts DESC LIMIT ?",
                               (since, until or time.time() + 1, mode, mode, agent, agent, lang, lang, limit))
        return [{"id": i, "ts": ts, "question": self._dec(q), "mode": m, "agent": a, "lang": la, "cached": bool(c),
                 "degraded": d, "guard": g, "sources": json.loads(so), "trace": json.loads(tr), "ms": ms}
                for i, ts, q, m, a, la, c, d, g, so, tr, ms in rows]

    def add_usage(self, model: str, tokens: dict, cost: float, labels: dict):
        self._exec("INSERT INTO llm_usage(ts, model, plugin, agent, purpose, input, output, cache_read, cache_write, "
                   "cost) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (time.time(), model, labels.get("plugin", "other"), labels.get("agent", ""),
                    labels.get("purpose", "other"), tokens.get("input", 0), tokens.get("output", 0),
                    tokens.get("cache_read", 0), tokens.get("cache_write", 0), cost))

    def spend(self, since: float) -> float:
        return self.db.execute("SELECT COALESCE(SUM(cost), 0) FROM llm_usage WHERE ts >= ?", (since,)).fetchone()[0]

    def usage_breakdown(self, since: float, by: str) -> list[dict]:
        if by not in ("plugin", "purpose", "agent", "model"):
            raise ValueError(by)
        rows = self.db.execute(f"SELECT {by}, COUNT(*), SUM(input), SUM(output), SUM(cost) FROM llm_usage "  # nosec B608 — allowlisted column
                               f"WHERE ts >= ? GROUP BY {by} ORDER BY SUM(cost) DESC", (since,))
        return [{by: k, "calls": n, "input": i or 0, "output": o or 0, "cost": round(c or 0, 6)} for k, n, i, o, c in rows]

    def daily_spend(self, days: int = 30) -> list[dict]:
        since = time.time() - days * 86400
        rows = self.db.execute("SELECT date(ts, 'unixepoch'), SUM(cost) FROM llm_usage WHERE ts >= ? "
                               "GROUP BY 1 ORDER BY 1", (since,))
        return [{"day": d, "cost": round(c, 6)} for d, c in rows]

    def alert_once(self, period: str, threshold: int) -> bool:
        """True the first time a budget threshold is crossed in a period (e.g. 'day:2026-10-02', 80)."""
        cur = self._exec("INSERT OR IGNORE INTO budget_alerts VALUES (?,?,?)", (period, threshold, time.time()))
        return cur.rowcount > 0

    def log_action(self, actor: str, action: str, detail: str = ""):
        self._exec("INSERT INTO admin_actions(ts, actor, action, detail) VALUES (?,?,?,?)",
                   (time.time(), pseudonym(actor) if actor else "api-key", action, redact(detail)[:300]))

    def actions(self, limit: int = 100) -> list[dict]:
        return [{"ts": ts, "actor": a, "action": ac, "detail": d} for ts, a, ac, d in self.db.execute(
            "SELECT ts, actor, action, detail FROM admin_actions ORDER BY ts DESC LIMIT ?", (limit,))]

    # Retention
    def purge(self, now: float | None = None, days: int | None = None) -> int:
        days = days or int(os.getenv("AGENTKIT_LOG_RETENTION_DAYS", "30"))
        cutoff = (now or time.time()) - days * 86400
        removed = 0
        with self.lock, self.db:
            old = [r[0] for r in self.db.execute("SELECT id FROM conversations WHERE ts < ?", (cutoff,))]
            for cid in old:
                self.db.execute("DELETE FROM turns WHERE conversation=?", (cid,))
            for table in RETENTION_TABLES:
                removed += self.db.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,)).rowcount  # nosec B608 — fixed table names
        return removed
