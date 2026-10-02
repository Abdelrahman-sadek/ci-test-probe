"""The 12 features from the multi-model review: feedback, history, live data, notices/dates, dialect evals,
OCR layout/confidence/corrections/preprocessing, handoff, librarian routing, accounts, special collections."""
import datetime
import json
import os
from types import SimpleNamespace as NS

import jwt
import pymupdf
import pytest
from fastapi.testclient import TestClient

from agentkit.api import create_app
from agentkit.appdb import AppDB
from agentkit.chat import LibraryChat
from agentkit.connectors import AlmaAccount, LibCal
from agentkit.jobs import JobQueue
from agentkit.llm import FakeLLM
from agentkit.ocr import layout_text, ocr_png, page_confidence, preprocess
from agentkit.rag import Index, ingest
from agentkit.services import Handoff, match_librarian

TODAY = datetime.date.today().isoformat()
SECRET = "k" * 32


def token(user, groups=()):
    return {"Authorization": "Bearer " + jwt.encode({"sub": user, "groups": list(groups)}, SECRET, algorithm="HS256")}


@pytest.fixture
def app(monkeypatch, seed_index, tmp_path):
    monkeypatch.setenv("AGENTKIT_AUTH", "jwt")
    monkeypatch.setenv("AGENTKIT_JWT_SECRET", SECRET)
    monkeypatch.setenv("AGENTKIT_ADMIN_KEY", "a" * 32)
    monkeypatch.setenv("AGENTKIT_RATE", "1000/min")
    db = AppDB(tmp_path / "app.db")
    idx = Index(seed_index.chunks)
    chat = LibraryChat(idx, FakeLLM(), appdb=db)
    jobs = JobQueue(idx, FakeLLM(), tmp_path / "idx.json")
    return TestClient(create_app(chat, jobs, tmp_path / "up")), chat, db, jobs


ADMIN = {"X-API-Key": "a" * 32}


# 1: feedback loop
def test_feedback_and_unanswered_become_eval_candidates(app):
    client, chat, db, _ = app
    ans = client.post("/api/ask", json={"question": "Can alumni borrow books?"}).json()
    assert client.post("/api/feedback", json={"answer_id": ans["id"], "rating": -1, "reason": "too short, mail me x@y.com",
                                              "question": "Can alumni borrow books?", "mode": ans["mode"]}).status_code == 200
    client.post("/api/ask", json={"question": "How much does printing cost?"})  # unanswered → recorded
    rep = client.get("/admin/api/feedback", headers=ADMIN).json()
    assert rep["down"] == 1 and "[EMAIL]" in rep["thumbs_down"][0]["reason"] and rep["unanswered"]
    cands = {c["question"] for c in client.get("/admin/api/eval-candidates", headers=ADMIN).json()}
    assert {"Can alumni borrow books?", "How much does printing cost?"} <= cands


# 2: saved conversations and searches
def test_history_is_per_user_and_requires_sign_in(app):
    client, *_ = app
    assert client.get("/api/saved").status_code == 401
    a = client.post("/api/ask", json={"question": "Can alumni borrow books?"}, headers=token("amira@aucegypt.edu")).json()
    cid = a["conversation_id"]
    client.post("/api/ask", json={"question": "and for how long?", "conversation_id": cid,
                                  "history": [{"role": "user", "content": "Can alumni borrow books?"}]},
                headers=token("amira@aucegypt.edu"))
    turns = client.get(f"/api/conversations/{cid}", headers=token("amira@aucegypt.edu")).json()
    assert len(turns) == 4 and turns[0]["content"] == "Can alumni borrow books?"
    assert client.get(f"/api/conversations/{cid}", headers=token("omar@aucegypt.edu")).json() == []  # isolation
    hijack = client.post("/api/ask", json={"question": "hi", "conversation_id": cid}, headers=token("omar@aucegypt.edu"))
    assert hijack.status_code == 403
    sid = client.post("/api/saved", json={"question": "theses?", "answer": "Knowledge Fountain"},
                      headers=token("amira@aucegypt.edu")).json()["id"]
    assert [s["id"] for s in client.get("/api/saved", headers=token("amira@aucegypt.edu")).json()] == [sid]
    assert client.delete(f"/api/saved/{sid}", headers=token("amira@aucegypt.edu")).json()["deleted"]


def test_appdb_encrypts_and_purges(tmp_path):
    from cryptography.fernet import Fernet
    db = AppDB(tmp_path / "e.db", key=Fernet.generate_key().decode())
    db.add_turn("u", None, "user", "my secret research topic")
    assert b"secret research" not in (tmp_path / "e.db").read_bytes()
    assert db.conversations("u")[0]["title"].startswith("my secret")
    assert db.purge(now=9e12) >= 1 and db.conversations("u") == []


# 3: live hours and rooms (LibCal)
def fake_libcal(status="open"):
    def fetch(url, data=None, headers=None):
        if "oauth" in url:
            return {"access_token": "t", "expires_in": 3600}
        if "/hours/" in url:
            return [{"name": "Main Library", "dates": {TODAY: {"status": status, "hours": [{"from": "8:00am", "to": "10:00pm"}]}}}]
        return [{"name": "Group Room 3", "availability": [{"from": "x", "to": "y"}]}]
    return LibCal("https://auc.libcal.test", "id", "secret", "1", space_lid="2", fetch=fetch)


def test_live_hours_answer_and_not_cached(seed_index):
    from agentkit.cache import AnswerCache
    chat = LibraryChat(Index(seed_index.chunks), FakeLLM(), libcal=fake_libcal(), cache=AnswerCache())
    a = chat.ask("What time does the main library close today?")
    assert a.sources[0][1].method == "live-hours" and "10:00pm" in a.text
    assert chat.ask("What time does the main library close today?") is not a  # live answers bypass the cache
    rooms = chat.ask("Can I book a study room today?")
    assert any(c.method == "live-rooms" for _, c in rooms.hits)


# 4: effective dates and pinned notices
def test_expired_content_hidden_and_urgent_notice_pinned(tmp_path, seed_index):
    old = tmp_path / "ramadan.md"
    old.write_text("---\ntitle: Ramadan hours\nvalid_to: 2020-01-01\n---\n# Ramadan\n\n## Hours\n"
                   "During Ramadan the library closes at four in the afternoon every day.\n")
    idx, _ = ingest([old], FakeLLM(), Index(seed_index.chunks))
    assert not any("Ramadan" in c.title for _, c in idx.search("Ramadan library closes", 10))
    db = AppDB(":memory:")
    db.add_notice("Library closed Thursday", "The Main Library is closed this Thursday for a national holiday.",
                  valid_from=TODAY, valid_to=TODAY)
    a = LibraryChat(idx, FakeLLM(), appdb=db).ask("Is the library open on Thursday?")
    assert a.hits[0][1].method == "notice" and "closed" in a.text.lower()


def test_notice_admin_api(app):
    client, *_ = app
    r = client.post("/admin/api/notices", headers=ADMIN, json={"title": "Exam hours", "body": "Open until midnight during finals.",
                                                                "valid_from": TODAY})
    nid = r.json()["id"]
    assert any(n["id"] == nid for n in client.get("/admin/api/notices", headers=ADMIN).json())
    assert client.post("/admin/api/notices", json={"title": "x", "body": "y"}).status_code == 403


# 6: OCR layout, tables, confidence
def _two_column_pdf(path):
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_textbox(pymupdf.Rect(40, 60, 280, 400), "LEFT column first paragraph about borrowing rules.", fontsize=11)
    pg.insert_textbox(pymupdf.Rect(320, 60, 560, 400), "RIGHT column second paragraph about renewals online.", fontsize=11)
    pg.insert_textbox(pymupdf.Rect(40, 410, 280, 600), "LEFT column third paragraph continues the text.", fontsize=11)
    pg.insert_textbox(pymupdf.Rect(320, 410, 560, 600), "RIGHT column fourth paragraph ends the page.", fontsize=11)
    doc.save(path)
    return path


def test_layout_reads_columns_in_order(tmp_path):
    with pymupdf.open(_two_column_pdf(tmp_path / "cols.pdf")) as doc:
        text = layout_text(doc[0])
    order = [text.index(w) for w in ("LEFT column first", "LEFT column third", "RIGHT column second", "RIGHT column fourth")]
    assert order == sorted(order)


def test_confidence_signals_and_model_confidence():
    assert page_confidence("A clean, ordinary sentence about the library.") > 0.9
    assert page_confidence("[illegible] [illegible] word [illegible]") < 0.5
    assert page_confidence("") == 0.0
    text, method, conf = ocr_png(_png(), FakeLLM(ocr_text="Faded page text.\nCONFIDENCE: low"))
    assert text == "Faded page text." and method == "ocr-claude" and conf == 0.45


def _png():
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_text((50, 80), "Scanned line", fontsize=14)
    return pg.get_pixmap(dpi=72).tobytes("png")


# 7: correction queue
def test_low_confidence_page_corrected_and_reindexed(app, tmp_path):
    client, chat, _, jobs = app
    scan = tmp_path / "scan.png"
    scan.write_bytes(_png())
    jobs.llm.ocr_text = "[illegible] [illegible] reading room [illegible] shelves [illegible] floor [illegible]"
    jobs.wait(jobs.submit([str(scan)]))
    cors = client.get("/admin/api/corrections", headers=ADMIN).json()
    assert cors and cors[0]["origin"] == str(scan) and cors[0]["confidence"] < 0.7
    r = client.post("/admin/api/corrections", headers=ADMIN, json={"origin": str(scan), "page": 1,
                    "text": "The manuscripts reading room opens at nine on weekdays for researchers."})
    jobs.wait(r.json()["job"])
    hit = chat.index.search("manuscripts reading room opens", 3)[0][1]
    assert hit.method == "corrected" and hit.confidence == 1.0


# 8: preprocessing, handwriting, scan view
def test_preprocess_and_handwriting_prompt():
    from PIL import Image
    import io
    from agentkit.llm import OCR_PROMPT
    out = Image.open(io.BytesIO(preprocess(_png())))
    assert out.mode == "L" and out.width >= 1500 and "[handwritten:" in OCR_PROMPT


def test_page_image_only_for_indexed_visible_pdfs(app, tmp_path, pdfs):
    client, chat, _, jobs = app
    jobs.llm.ocr_text = "Group study rooms can be booked online for two hours per day."
    jobs.wait(jobs.submit([str(pdfs[0])]))
    ok = client.get("/api/page-image", params={"origin": str(pdfs[0]), "page": 1})
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    assert client.get("/api/page-image", params={"origin": "/etc/passwd", "page": 1}).status_code == 404
    assert client.get("/api/page-image", params={"origin": str(pdfs[0]), "page": 9}).status_code == 404


# 9: real handoff
def test_handoff_ticket_consent_and_routing(app):
    client, chat, db, _ = app
    assert client.post("/api/handoff", json={"question": "How much does printing cost?", "email": "s@aucegypt.edu"}).status_code == 422
    r = client.post("/api/handoff", json={"question": "Mamluk history sources?", "email": "s@aucegypt.edu", "consent": True,
                                          "history": [{"role": "user", "content": "my id is 29801011234567"}]}).json()
    assert r["ticket"] and r["routed_to"] == "Arab and Middle East studies"
    t = db.tickets("question")[0]
    assert t["email"] == "s@aucegypt.edu" and "29801011234567" not in t["summary"]


def test_handoff_email_and_libanswers_backends(monkeypatch):
    sent = []

    class FakeSMTP:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent.append("tls")
        def send_message(self, m): sent.append(m)

    monkeypatch.setenv("AGENTKIT_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("AGENTKIT_HANDOFF_EMAIL", "reference@library.test")
    h = Handoff(AppDB(":memory:"), libcal=fake_libcal("closed"), smtp=FakeSMTP)
    r = h.create("question", "Where are theses?", email="s@x.edu", consent=True)
    assert r["delivered_via"] == "email" and "closed" in r["message"] and sent[0] == "tls"
    posts = []
    monkeypatch.setenv("AGENTKIT_LIBANSWERS_URL", "https://auc.libanswers.test")
    monkeypatch.setenv("AGENTKIT_LIBANSWERS_TOKEN", "t")
    monkeypatch.setenv("AGENTKIT_LIBANSWERS_QUEUE", "42")
    h2 = Handoff(AppDB(":memory:"), post=lambda url, data, headers: posts.append((url, data)) or {"id": 7})
    assert h2.create("question", "Help with APA")["delivered_via"] == "libanswers" and posts[0][1]["quid"] == "42"


# 10: subject librarians + consultations
def test_librarian_routing_and_consultation_action(chat):
    assert match_librarian("sources on renewable energy engineering")["subject"] == "Science and engineering"
    a = chat.ask("I need peer-reviewed articles on water scarcity in Egypt")
    kinds = {x["type"] for x in a.actions}
    assert a.mode == "strategy" and {"handoff", "librarian"} <= kinds
    assert next(x for x in a.actions if x["type"] == "librarian")["subject"] == "Social sciences and law"


# 11: read-only account
ALMA = {"loans": {"item_loan": [{"loan_id": "L1", "title": "Palace Walk", "due_date": "2026-10-20Z", "loan_status": "ACTIVE"}]},
        "requests": {"user_request": [{"title": "Sugar Street", "request_status": "IN_PROCESS", "request_type": "HOLD"}]},
        "resource-sharing-requests": {"user_resource_sharing_request": [{"title": "ILL book", "status": {"desc": "Shipped"}}]},
        "fees": {"total_sum": 15}}


def test_account_answers_are_direct_private_and_read_only(seed_index, tmp_path, monkeypatch):
    calls = []
    alma = AlmaAccount("https://alma.test", "key", fetch=lambda url, data=None, headers=None: calls.append(url) or
                       ALMA[url.split("/")[-1].split("?")[0]])
    chat = LibraryChat(Index(seed_index.chunks), FakeLLM(), account=alma, log_path=tmp_path / "log.jsonl")
    assert chat.ask("What are my loans?").actions == [{"type": "signin"}]
    a = chat.ask("What are my loans?", user="amira@aucegypt.edu")
    assert a.mode == "account" and "Palace Walk — due 2026-10-20" in a.text and "15 EGP" in a.text and "Shipped" in a.text
    assert chat.llm.calls == [] and not (tmp_path / "log.jsonl").exists()  # no model call, nothing logged
    assert "amira%40aucegypt.edu" in calls[0]
    with pytest.raises(PermissionError):
        alma.renew("amira@aucegypt.edu", "L1")
    monkeypatch.setenv("AGENTKIT_ALMA_ALLOW_RENEW", "1")
    assert chat.ask("renew my books", user="amira@aucegypt.edu").actions[0]["type"] == "renew"


# 12: special collections
EAD = """<ead xmlns="urn:isbn:1-931666-22-9"><archdesc level="collection"><did><unittitle>Cairo Photographs Collection</unittitle>
<unitdate>1920-1950</unitdate><unitid>RBSCL-PH-12</unitid><physdesc><extent>12 boxes</extent></physdesc></did>
<scopecontent><p>Photographs of Cairo streets, mosques and markets.</p></scopecontent>
<userestrict><p>Reproduction requires written permission.</p></userestrict>
<dsc><c01><did><unittitle>Khan el-Khalili</unittitle><unitdate>1925</unitdate><container>Box 3</container></did></c01></dsc>
</archdesc></ead>"""


def test_finding_aid_ingested_and_dtd_refused(tmp_path):
    f = tmp_path / "photos.xml"
    f.write_text(EAD)
    idx, rep = ingest([f], FakeLLM())
    hit = idx.search("photographs of Cairo markets 1920", 3)[0][1]
    assert "finding aid" in hit.title and rep[0]["chunks"] >= 3
    bad = tmp_path / "bomb.xml"
    bad.write_text('<!DOCTYPE x [<!ENTITY a "aaaa">]><ead>&a;</ead>')
    assert "DTD" in ingest([bad], FakeLLM())[1][0]["error"]


def test_special_collections_request_flow(app):
    client, chat, db, _ = app
    assert client.get("/request").status_code == 200
    a = chat.ask("Can I see historical photographs in the rare books library?")
    assert {"type": "request", "url": "/request"} in a.actions
    body = {"request_type": "reproduction", "collection": "Cairo Photographs Collection", "items": "Box 3",
            "name": "Researcher", "email": "r@uni.edu", "consent": True, "affiliation": "external"}
    r = client.post("/api/requests/special-collections", json=body).json()
    assert r["ticket"] and "permission" in r["rights_notice"].lower()
    assert db.tickets("rbscl-reproduction")[0]["collection"] == "Cairo Photographs Collection"
    assert client.post("/api/requests/special-collections", json={**body, "consent": False}).status_code == 422
