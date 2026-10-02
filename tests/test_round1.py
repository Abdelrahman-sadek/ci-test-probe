"""Review round 1: resilience, data rights, operations, conflicts, snapshots, OCR limits, answer style."""
import os
import shutil
import subprocess
import sys

import pytest

from agentkit import style
from agentkit.appdb import AppDB
from agentkit.chat import LibraryChat, referral, related_topics, resolve_conflicts
from agentkit.llm import FakeLLM, Grounded, LLM, ResilientLLM
from agentkit.rag import Chunk, Index, freshness, ingest
from agentkit.services import Handoff, escalate_overdue
from test_features import ADMIN, app, token  # noqa: F401  (fixture reuse)


class Broken(LLM):
    live = True

    def __init__(self):
        self.calls = 0

    def answer(self, system, question, sources):
        self.calls += 1
        raise ConnectionError("api down")

    def stream_answer(self, system, question, sources):
        raise ConnectionError("api down")
        yield  # pragma: no cover


def test_outage_degrades_to_extractive_and_breaker_opens(seed_index):
    inner = Broken()
    llm = ResilientLLM(inner, cooldown=60)
    chat = LibraryChat(seed_index, llm, cache=None)
    for _ in range(3):
        ans = chat.ask("How many books can undergraduates borrow?")
        assert ans.degraded == "outage" and ans.mode == "answer" and "20" in ans.text
    assert llm.status() == "outage" and inner.calls == 3
    chat.ask("Can alumni borrow books?")
    assert inner.calls == 3  # breaker open: the broken API is not called again during cooldown


def test_budget_switches_to_extractive(seed_index, monkeypatch):
    llm = ResilientLLM(FakeLLM(), budget_usd=0.0)
    assert llm.status() == "budget"
    g = llm.answer("s", "How many books can alumni borrow?",
                   [{"title": "Borrow", "blocks": ["Borrow", "Alumni can borrow up to 5 books for 14 days."]}])
    assert g.degraded == "budget" and "5 books" in g.text


def test_data_export_and_delete(app):  # noqa: F811
    client, chat, db, _ = app
    h = token("student@aucegypt.edu")
    client.post("/api/ask", json={"question": "How long can alumni borrow books?"}, headers=h)
    client.post("/api/handoff", json={"question": "Need help with a thesis", "consent": False}, headers=h)
    data = client.get("/api/me/data", headers=h).json()
    assert data["conversations"] and data["conversations"][0]["turns"]
    assert client.get("/api/me/data").status_code == 401
    assert client.delete("/api/me/data", headers=h).json()["deleted_rows"] >= 2
    assert client.get("/api/me/data", headers=h).json()["conversations"] == []


def test_handoff_backend_failure_still_saves_ticket(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTKIT_SMTP_HOST", "smtp.example.edu")
    monkeypatch.setenv("AGENTKIT_HANDOFF_EMAIL", "ref@example.edu")

    def bad_smtp():
        raise OSError("connection refused")
    db = AppDB(tmp_path / "a.db")
    res = Handoff(db, smtp=bad_smtp).create("handoff", "Where are the Egyptian newspapers?")
    assert res["delivered_via"] == "queue" and "saved" in res["message"]
    t = db.tickets()[0]
    assert t["status"] == "queued" and t["delivery_failures"] == ["email: OSError"]


def test_overdue_tickets_escalate_and_workload(tmp_path):
    db = AppDB(tmp_path / "a.db")
    old = db.add_ticket("handoff", {"question": "q"}, "History")
    db.add_ticket("handoff", {"question": "q2"}, "History")
    db._exec("UPDATE tickets SET ts = ts - 72*3600 WHERE id=?", (old,))
    assert escalate_overdue(db) == [old]
    w = db.workload()["History"]
    assert w["open"] == 2 and w["oldest_open_hours"] >= 72
    assert escalate_overdue(db) == []  # escalated tickets are not escalated twice


def test_conflicting_sources_keep_the_newer_one():
    old = Chunk(id="a", source="old.md", title="Old loans", section="", page=1, lang="en", method="text", text="Alumni borrow 3 books for 7 days.",
                updated="2024-01-01", tokens=["alumni", "borrow", "books", "days"])
    new = Chunk(id="b", source="new.md", title="Loans", section="", page=1, lang="en", method="text", text="Alumni borrow 5 books for 14 days.",
                updated="2026-09-01", tokens=["alumni", "borrow", "books", "days"])
    hits, notes = resolve_conflicts([(1.0, old), (0.9, new)])
    assert [c.id for _, c in hits] == ["b"] and "newer" in notes[0]


def test_related_topics_and_referral():
    assert related_topics("How do I renew a book?")
    office = referral("How do I register for courses?")
    assert office and office.get("url")


def test_snapshot_skips_empty_and_rollback_restores(tmp_path, seed_index):
    path = tmp_path / "idx.json"
    empty = Index([])
    empty.save(path)
    assert empty.snapshot(path) is None  # nothing worth restoring
    idx = Index(seed_index.chunks)
    idx.save(path)
    snap = idx.snapshot(path, keep=2)
    assert snap and snap.exists()
    path.write_text("{}")  # simulate a bad ingest
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    out = subprocess.run([sys.executable, "-m", "agentkit", "--index", str(path), "rollback"],
                         capture_output=True, text=True, env=env)
    assert "Restored" in out.stdout, out.stderr
    assert len(Index.load(path).chunks) == len(seed_index.chunks)


def test_freshness_flags_old_sources(tmp_path):
    idx = Index()
    ingest(["knowledge/auc-library/pages/library-hours.md"], FakeLLM(), idx)
    report = freshness(idx, stale_days=0, now=10**11)
    assert report["stale_sources"] and report["stale_sources"][0]["origin"].endswith("library-hours.md")
    assert freshness(idx, stale_days=365)["stale_sources"] == []


def test_ocr_page_cap(tmp_path, monkeypatch):
    import pymupdf
    from agentkit import ocr
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()  # blank pages have no text layer, so they need OCR
    doc.save(pdf)
    monkeypatch.setattr(ocr, "OCR_MAX_PAGES", 1)
    pages = ocr.extract(pdf, FakeLLM(ocr_text="Scanned text\nCONFIDENCE: high"))
    assert [p.method for p in pages].count("ocr-skipped") == 2


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_diacritized_arabic_lands_in_correction_queue():
    from PIL import Image, ImageDraw, ImageFont
    import io
    from agentkit.ocr import LOW_CONFIDENCE, _tesseract
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    img = Image.new("L", (900, 120), 255)
    ImageDraw.Draw(img).text((20, 30), "الْمَكْتَبَةُ مَفْتُوحَةٌ لِلطُّلَّابِ", font=font, fill=0)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    res = _tesseract(buf.getvalue())
    assert res is None or res[1] < LOW_CONFIDENCE  # never silently trusted as clean text


def test_searchable_pdf_has_invisible_text_layer(tmp_path, pdfs):
    import pymupdf
    from agentkit.accessible import export_searchable_pdf
    out = tmp_path / "searchable.pdf"
    export_searchable_pdf(pdfs[0], FakeLLM(ocr_text="Hidden layer text\nCONFIDENCE: high"), out)
    assert "Hidden layer text" in pymupdf.open(out)[0].get_text()


def test_answer_style_filter():
    raw = "Great question! Alumni can borrow 5 books [1]. I hope this helps! Let me know if you need more."
    assert style.findings(raw)
    assert style.clean(raw) == "Alumni can borrow 5 books [1]."
    ar = "سؤال رائع! الخريجين يستعيروا 5 كتب [1]. لا تتردد في السؤال."
    assert style.clean(ar) == "الخريجين يستعيروا 5 كتب [1]."
    assert style.clean("Feel free to cite this [2].") == "Feel free to cite this [2]."  # cited text stays
    assert style.findings("A seamless, robust service.") == ["buzzword: seamless", "buzzword: robust"]


def test_chat_strips_filler_from_model_output(seed_index):
    class Chatty(FakeLLM):
        def answer(self, system, question, sources):
            assert "No greeting" in system  # style rules reach the model
            g = super().answer(system, question, sources)
            return Grounded("Great question! " + g.text + " Hope this helps!", g.cited, g.quotes)
    ans = LibraryChat(seed_index, Chatty(), cache=None).ask("How many books can undergraduates borrow?")
    assert ans.mode == "answer" and not style.findings(ans.text)


def test_privacy_page_served(app):  # noqa: F811
    client = app[0]
    r = client.get("/privacy")
    assert r.status_code == 200 and "Privacy" in r.text
