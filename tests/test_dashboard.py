"""Plan 7 phases A and B: persistent usage and budget alerts, cost attribution, roles, audit log, dashboard
data, knowledge-gap clusters."""
from types import SimpleNamespace as NS

import jwt
import pytest
from fastapi.testclient import TestClient

from agentkit.api import create_app
from agentkit.appdb import AppDB
from agentkit.budget import BudgetMonitor, attach
from agentkit.chat import LibraryChat
from agentkit.gaps import detect
from agentkit.llm import FakeLLM, Grounded, ResilientLLM
from agentkit.metrics import METRICS
from agentkit.rag import Index

SECRET = "s" * 32


def tok(user, groups):
    return {"Authorization": "Bearer " + jwt.encode({"sub": user, "groups": groups}, SECRET, algorithm="HS256")}


class Billed(FakeLLM):
    """Offline model that reports token usage like the real client does."""
    live = True

    def answer(self, system, question, sources):
        METRICS.record_usage("claude-opus-5-5", NS(input_tokens=1000, output_tokens=100))
        return super().answer(system, question, sources)

    def complete(self, system, user, *, fast=False, max_tokens=None):
        METRICS.record_usage("claude-haiku-4-5", NS(input_tokens=200, output_tokens=5))
        if system.startswith("You check search results"):
            return "1,2,3"
        return super().complete(system, user, fast=fast, max_tokens=max_tokens)


@pytest.fixture
def billed(seed_index, tmp_path):
    db = AppDB(tmp_path / "app.db")
    llm = ResilientLLM(Billed())
    chat = LibraryChat(Index(seed_index.chunks), llm, appdb=db, cache=None)
    sent = []
    chat.budget = attach(llm, db, notify=sent.append)
    yield chat, db, sent
    METRICS.sinks.clear()


def test_usage_is_attributed_and_survives_restart(billed, tmp_path):
    chat, db, _ = billed
    chat.ask("How many books can undergraduates borrow?")
    plugins = {r["plugin"]: r for r in db.usage_breakdown(0, "plugin")}
    purposes = {r["purpose"] for r in db.usage_breakdown(0, "purpose")}
    assert "auc-library" in plugins and {"answer", "grade"} <= purposes
    total = db.spend(0)
    assert total > 0 and abs(sum(r["cost"] for r in plugins.values()) - total) < 1e-9
    assert AppDB(tmp_path / "app.db").spend(0) == total  # a restart keeps today's spend


def test_thresholds_alert_once_and_brake_then_cap(billed):
    chat, db, sent = billed
    chat.budget.caps["day"] = 0.0075  # one answer + grade ≈ $0.0062 → 83 %
    chat.ask("How many books can undergraduates borrow?")
    assert [m.split("%")[0][-2:] for m in sent] == ["50", "80"]
    assert chat.llm.brake and chat.llm.status() == "ok"
    ans = chat.ask("Can alumni borrow books?")
    assert "grade" not in [s["step"] for s in ans.trace]  # soft brake: optional calls stop first
    chat.ask("How long can graduate students keep books?")
    assert chat.llm.status() == "budget"
    assert sum("80%" in m for m in sent) == 1  # never repeated in the same day
    assert chat.ask("Can alumni borrow books?").degraded == "budget"


def test_answers_are_recorded_redacted(billed):
    chat, db, _ = billed
    chat.ask("my email is someone@aucegypt.edu, can alumni borrow books?", user="x@aucegypt.edu")
    row = db.answers()[0]
    assert "someone@aucegypt.edu" not in row["question"] and row["mode"] == "answer" and row["trace"]
    assert row["sources"]


@pytest.fixture
def app(seed_index, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTKIT_AUTH", "jwt")
    monkeypatch.setenv("AGENTKIT_JWT_SECRET", SECRET)
    monkeypatch.setenv("AGENTKIT_VIEWER_GROUPS", "library-viewers")
    monkeypatch.setenv("AGENTKIT_RATE", "1000/min")
    db = AppDB(tmp_path / "app.db")
    chat = LibraryChat(Index(seed_index.chunks), FakeLLM(), appdb=db, cache=None)
    chat.budget = BudgetMonitor(db, notify=lambda m: None)
    return TestClient(create_app(chat)), chat, db


def test_roles_gate_tabs(app):
    client, chat, db = app
    viewer, staff, admin = (tok("v@x", ["library-viewers"]), tok("s@x", ["library-staff"]),
                            tok("a@x", ["library-staff-admin"]))
    assert client.get("/admin/api/role", headers=viewer).json()["tabs"] == ["today", "trends", "conversations", "gaps",
                                                                          "evaluations"]
    assert client.get("/admin/api/dashboard/costs", headers=viewer).status_code == 403
    assert client.get("/admin/api/dashboard/tickets", headers=staff).status_code == 200
    assert client.get("/admin/api/dashboard/system", headers=staff).status_code == 403
    assert client.get("/admin/api/dashboard/system", headers=admin).status_code == 200
    assert client.get("/admin/api/dashboard/today").status_code == 403
    assert client.get("/admin/api/dashboard/nope", headers=admin).status_code == 404
    assert client.post("/admin/api/notices", json={"title": "t", "body": "b"}, headers=viewer).status_code == 403


def test_every_tab_returns_data_and_today_matches(app):
    client, chat, db = app
    admin = tok("a@x", ["library-staff-admin"])
    for q in ["Can alumni borrow books?", "Who won the match?", "ممكن اجدد الكتاب بالتليفون؟"]:
        client.post("/api/ask", json={"question": q})
    names = client.get("/admin/api/role", headers=admin).json()["tabs"]
    assert len(names) == 13
    for n in names:
        r = client.get(f"/admin/api/dashboard/{n}", headers=admin)
        assert r.status_code == 200, (n, r.text)
    today = client.get("/admin/api/dashboard/today", headers=admin).json()["data"]
    assert today["questions"] == 3 and today["modes"] == {"answer": 2, "handoff": 1}
    conv = client.get("/admin/api/dashboard/conversations?mode=handoff", headers=admin).json()["data"]["rows"]
    assert [r["mode"] for r in conv] == ["handoff"]


def test_staff_actions_are_audited(app):
    client, chat, db = app
    staff, admin = tok("s@x", ["library-staff"]), tok("a@x", ["library-staff-admin"])
    client.post("/admin/api/notices", json={"title": "Closed Friday", "body": "b"}, headers=staff)
    client.get("/admin/api/dashboard/conversations", headers=staff)
    client.post("/admin/api/maintenance", json={"on": True}, headers=admin)
    acts = client.get("/admin/api/dashboard/security", headers=admin).json()["data"]["admin_actions"]
    assert {a["action"] for a in acts} >= {"notice-add", "view-conversations", "maintenance"}
    assert all("@" not in a["actor"] for a in acts)  # pseudonyms only


def _seed_gap(db, users=3):
    qs = ["What is the fine for a lost book?", "lost book fine amount", "كام غرامة الكتاب الضايع؟",
          "how much do I pay if I lose a library book fine", "el gharama bta3t el ketab el dayi3 kam?"]
    for i, q in enumerate(qs):
        db.add_unanswered(q, "handoff", "en", "auc-library-concierge", user=f"user{i % users}@aucegypt.edu")
    db.add_unanswered("Who won the football match?", "handoff", "en", "auc-library-concierge", user="z@x")


def test_gap_clusters_group_languages_and_hide_small(seed_index, tmp_path):
    db = AppDB(tmp_path / "g.db")
    _seed_gap(db)
    chat = LibraryChat(seed_index, FakeLLM())
    from agentkit.arabic import detect_lang
    rep = detect(db, seed_index, expand=lambda q: chat._expand(q, detect_lang(q)))
    big = rep["clusters"][0]
    assert big["people"] >= 3 and "fine" in " ".join(big["terms"] + [big["label"]])
    assert rep["hidden_small"] >= 1  # the single football question is not shown
    assert all("football" not in " ".join(c["examples"]) for c in rep["clusters"])
    db2 = AppDB(tmp_path / "g2.db")
    _seed_gap(db2, users=2)
    assert all(c["people"] >= 3 for c in detect(db2, seed_index)["clusters"])


def test_gap_actions_create_ticket_and_candidates(app, tmp_path, monkeypatch):
    client, chat, db = app
    monkeypatch.setenv("AGENTKIT_EVAL_CANDIDATES", str(tmp_path / "cand.md"))
    _seed_gap(db)
    staff = tok("s@x", ["library-staff"])
    gaps = client.get("/admin/api/dashboard/gaps", headers=staff).json()["data"]["clusters"]
    cid = gaps[0]["id"]
    t = client.post(f"/admin/api/gaps/{cid}/ticket", headers=staff).json()["ticket"]
    assert any(x["id"] == t and x["kind"] == "content-gap" for x in db.tickets())
    assert client.post(f"/admin/api/gaps/{cid}/candidate", headers=staff).json()["appended"] >= 1
    assert "gap-" in (tmp_path / "cand.md").read_text(encoding="utf-8")
