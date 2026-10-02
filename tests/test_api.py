import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from agentkit.api import create_app
from agentkit.chat import LibraryChat
from agentkit.jobs import JobQueue
from agentkit.llm import FakeLLM
from agentkit.rag import Index


@pytest.fixture
def env(monkeypatch, seed_index, tmp_path):
    monkeypatch.setenv("AGENTKIT_ADMIN_KEY", "a" * 32)
    monkeypatch.setenv("AGENTKIT_RATE", "1000/min")
    idx = Index(seed_index.chunks)
    chat = LibraryChat(idx, FakeLLM())
    jobs = JobQueue(idx, FakeLLM(), tmp_path / "idx.json")
    return TestClient(create_app(chat, jobs, tmp_path / "uploads")), chat, jobs


ADMIN = {"X-API-Key": "a" * 32}


def test_ask_and_security_headers(env):
    client, *_ = env
    r = client.post("/api/ask", json={"question": "Can alumni borrow books?"})
    assert r.status_code == 200 and r.json()["mode"] == "answer"
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and r.headers["x-content-type-options"] == "nosniff"


def test_stream_sse(env):
    client, *_ = env
    with client.stream("POST", "/api/ask/stream", json={"question": "Can alumni borrow books?"}) as r:
        body = "".join(r.iter_text())
    events = [e for e in body.split("\n\n") if e]
    done = json.loads(events[-1].split("data: ", 1)[1])
    assert events[-1].startswith("event: done") and done["sources"] and len(events) > 2


def test_limits_413_422_and_429(env, monkeypatch):
    client, chat, _ = env
    assert client.post("/api/ask", content=b"{" + b" " * 20000 + b"}",
                       headers={"content-type": "application/json"}).status_code == 413
    assert client.post("/api/ask", json={"question": ""}).status_code == 422
    monkeypatch.setenv("AGENTKIT_RATE", "2/min")
    limited = TestClient(create_app(chat))
    codes = [limited.post("/api/ask", json={"question": "hours?"}).status_code for _ in range(3)]
    assert codes[:2] == [200, 200] and codes[2] == 429


def test_login_required_and_jwt_access(env, monkeypatch, tmp_path):
    client, chat, jobs = env
    monkeypatch.setenv("AGENTKIT_AUTH", "jwt")
    monkeypatch.setenv("AGENTKIT_JWT_SECRET", "s" * 32)
    monkeypatch.setenv("AGENTKIT_REQUIRE_LOGIN", "1")
    assert client.post("/api/ask", json={"question": "hours?"}).status_code == 401
    doc = tmp_path / "staff.md"
    doc.write_text("---\ntitle: Staff rota\naccess: staff\n---\n# Rota\n\n## Desk\nThe circulation desk staff rota changes every Sunday morning.\n")
    jobs.wait(jobs.submit([str(doc)]))
    staff = jwt.encode({"sub": "s@aucegypt.edu", "groups": ["library-staff"]}, "s" * 32, algorithm="HS256")
    student = jwt.encode({"sub": "u@aucegypt.edu", "groups": ["students"]}, "s" * 32, algorithm="HS256")
    q = {"question": "When does the circulation desk staff rota change?"}
    as_staff = client.post("/api/ask", json=q, headers={"Authorization": f"Bearer {staff}"}).json()
    as_student = client.post("/api/ask", json=q, headers={"Authorization": f"Bearer {student}"}).json()
    titles = lambda a: {s["title"] for s in a["sources"]} | {h["title"] for h in a["retrieved"]}  # noqa: E731
    assert "Staff rota" in titles(as_staff) and "Staff rota" not in titles(as_student)


def test_admin_upload_job_review_and_metrics(env):
    client, chat, jobs = env
    assert client.get("/admin/api/stats").status_code == 403
    assert client.get("/metrics").status_code == 403
    page = b"# Printing\n\n## Cost\nPrinting costs one Egyptian pound per page at the ground floor."
    r = client.post("/admin/api/upload", headers=ADMIN, json={"filename": "../../printing.md",
                    "content_b64": base64.b64encode(page).decode(), "title": "Printing",
                    "url": "https://library.aucegypt.edu/printing", "access": "public"})
    assert r.status_code == 200 and r.json()["file"] == "printing.md"  # path traversal neutralised
    job = jobs.wait(r.json()["job"])
    assert job["status"] == "done" and job["report"][0]["chunks"] >= 1
    ans = client.post("/api/ask", json={"question": "How much does printing cost?"}).json()
    assert ans["mode"] == "answer" and ans["sources"][0]["url"] == "https://library.aucegypt.edu/printing"
    bad = client.post("/admin/api/upload", headers=ADMIN, json={"filename": "x.exe", "content_b64": "AA=="})
    assert bad.status_code == 415
    assert "agentkit_requests_total" in client.get("/metrics", headers=ADMIN).text
    assert client.get("/admin/api/stats", headers=ADMIN).json()["requests"] >= 1


def test_static_assets_and_traversal(env):
    client, *_ = env
    assert "AUC Library Assistant" in client.get("/").text
    assert client.get("/widget.js").status_code == 200 and client.get("/static/app.js").status_code == 200
    assert client.get("/static/..%2Fapi.py").status_code == 404
    assert client.get("/healthz").json()["status"] == "ok"


def test_embed_origins(env, monkeypatch, seed_index):
    monkeypatch.setenv("AGENTKIT_EMBED_ORIGINS", "https://library.aucegypt.edu")
    client = TestClient(create_app(LibraryChat(Index(seed_index.chunks), FakeLLM())))
    r = client.get("/embed")
    assert "frame-ancestors 'self' https://library.aucegypt.edu" in r.headers["content-security-policy"]
    pre = client.options("/api/ask", headers={"Origin": "https://library.aucegypt.edu",
                                              "Access-Control-Request-Method": "POST"})
    assert pre.headers["access-control-allow-origin"] == "https://library.aucegypt.edu"


def test_whatsapp_webhook(env, monkeypatch):
    client, chat, _ = env
    monkeypatch.setenv("AGENTKIT_WA_VERIFY_TOKEN", "vt")
    monkeypatch.setenv("AGENTKIT_WA_APP_SECRET", "appsecret")
    ok = client.get("/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"})
    assert ok.text == "42" and client.get("/whatsapp", params={"hub.verify_token": "no"}).status_code == 403
    sent = []
    monkeypatch.setattr("agentkit.whatsapp.WhatsAppSender.send", lambda self, to, text: sent.append((to, text)))
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "2010", "type": "text", "text": {"body": "Can alumni borrow books?"}},
        {"from": "2011", "type": "audio"}]}}]}]}
    raw = json.dumps(payload).encode()
    assert client.post("/whatsapp", content=raw, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 401
    sig = "sha256=" + hmac.new(b"appsecret", raw, hashlib.sha256).hexdigest()
    assert client.post("/whatsapp", content=raw, headers={"x-hub-signature-256": sig}).status_code == 200
    deadline = time.time() + 5
    while len(sent) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert sent[0][0] == "2010" and "14 days" in sent[0][1] and "voice" in sent[1][1]
