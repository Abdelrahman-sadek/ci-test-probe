"""Regenerate the README screenshots: python scripts/screenshots.py [--out docs/screenshots]

Starts the app on the seed corpus plus the fictional thesis fixture, fills the staff database with demo traffic
(fictional questions spread over two weeks), and captures the chat (English, Arabic, mobile, dark), the citation
export, the staff dashboard tabs and the forms with headless Chromium. Offline: answers come from the
extractive stand-in.
"""
import argparse
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("AGENTKIT_ALLOWED_DOMAINS", "")
KEY = "k" * 32


def build(tmp: Path):
    from agentkit.appdb import AppDB
    from agentkit.budget import attach
    from agentkit.chat import LibraryChat
    from agentkit.harvest import harvest
    from agentkit.llm import FakeLLM
    from agentkit.rag import Index, ingest
    pages = {False: (ROOT / "samples/fixtures/oai/page1.xml").read_bytes(),
             True: (ROOT / "samples/fixtures/oai/page2.xml").read_bytes()}

    def fetch(url):
        if "viewcontent" in url:
            raise OSError("offline")
        return pages["resumptionToken=page2token" in url]
    harvest("https://fount.aucegypt.edu/do/oai/", tmp / "theses", fetch=fetch)
    idx = Index()
    ingest([str(ROOT / "knowledge/auc-library/pages"), str(tmp / "theses")], FakeLLM(), idx, review=False)
    db = AppDB(tmp / "app.db")
    chat = LibraryChat(idx, FakeLLM(), appdb=db, cache=None)
    chat.budget = attach(chat.llm, db, notify=lambda m: None)
    chat.budget.caps.update(day=5.0, month=100.0)
    demo = ["How many books can alumni borrow?", "Can I renew a book online?", "ممكن الخريجين يستعيروا كتب؟",
            "Where is the rare books library?", "What ID do external visitors need?", "Theses advised by Laila Nassar",
            "momken a7gez ma3ad ma3 amin maktaba?", "Who won the match yesterday?", "Economics theses since 2020",
            "What are the SRC library hours?", "How long can undergraduates keep books?", "Write my essay for me"]
    now = time.time()
    for day in range(14):
        for j, q in enumerate(demo[: 4 + (day * 3) % 9]):
            chat.ask(q, user=f"student{j % 5}@aucegypt.edu")
        db._exec("UPDATE answers SET ts = ? WHERE ts > ?", (now - day * 86400 - 3600, now - 60))
    for i, q in enumerate(["What is the fine for a lost book?", "lost book fine amount", "كام غرامة الكتاب الضايع؟",
                           "how much do I pay if I lose a library book fine"]):
        db.add_unanswered(q, "handoff", "en", "auc-library-concierge", user=f"user{i}@aucegypt.edu")
    for i, (plugin, purpose, cost) in enumerate([("auc-library", "answer", 0.9), ("auc-library", "grade", 0.12),
                                                 ("auc-library", "rewrite", 0.05), ("auc-library", "critic", 0.18),
                                                 ("rag", "ocr", 0.6), ("rag", "contextualize", 0.3)]):
        db.add_usage("claude-opus-5-5" if purpose in ("answer", "ocr") else "claude-haiku-4-5",
                     {"input": 40000, "output": 3000}, cost, {"plugin": plugin, "purpose": purpose,
                                                              "agent": "auc-library-concierge"})
    for d in range(1, 10):
        db.add_usage("claude-opus-5-5", {"input": 30000, "output": 2000}, 0.4 + d * 0.05,
                     {"plugin": "auc-library", "purpose": "answer", "agent": "auc-research-assistant"})
        db._exec("UPDATE llm_usage SET ts = ? WHERE id = (SELECT MAX(id) FROM llm_usage)", (now - d * 86400,))
    db.add_ticket("handoff", {"question": "Do you have Al-Ahram issues from 1952?"}, "Arab and Middle East studies")
    db.add_ticket("special-collections", {"question": "Request to view a Mamluk manuscript"},
                  "Rare books, archives and special collections")
    db.add_feedback("x", 1, "", "How many books can alumni borrow?", "answer", "en", "student1@aucegypt.edu")
    db.log_action("staff@aucegypt.edu", "notice-add", "Library closed on 6 October")
    return chat


def serve(chat):
    import uvicorn
    from agentkit.api import create_app
    os.environ["AGENTKIT_ADMIN_KEY"] = KEY
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(chat), port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/screenshots")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as tmp:
        url, server = serve(build(Path(tmp)))
        exe = "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()

            def page(width=1280, height=900, dark=False):
                ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                                          color_scheme="dark" if dark else "light")
                return ctx.new_page()

            def ask(pg, q):
                pg.fill("#q", q)
                pg.press("#q", "Enter")
                pg.wait_for_selector(".msg[aria-busy='false']", timeout=15000)

            pg = page(1100, 900)
            pg.goto(url + "/")
            ask(pg, "How many books can alumni borrow?")
            pg.click(".cite summary")
            pg.click(".cite button")
            pg.wait_for_selector(".citeout:not([hidden])")
            pg.screenshot(path=out / "chat-en.png", full_page=True)

            pg = page(1100, 900)
            pg.goto(url + "/")
            pg.click("#lang")
            ask(pg, "ممكن الخريجين يستعيروا كتب؟")
            pg.screenshot(path=out / "chat-ar.png", full_page=True)

            pg = page(390, 844)
            pg.goto(url + "/")
            ask(pg, "Theses advised by Laila Nassar")
            pg.screenshot(path=out / "chat-mobile.png", full_page=True)

            pg = page(1100, 900, dark=True)
            pg.goto(url + "/")
            ask(pg, "Who won the match yesterday?")
            pg.screenshot(path=out / "chat-handoff-dark.png", full_page=True)

            pg = page(1280, 900)
            pg.goto(url + "/admin")
            pg.fill("#key", KEY)
            pg.dispatch_event("#key", "change")
            pg.wait_for_selector("#role:has-text('admin')")
            for name in ("today", "trends", "conversations", "gaps", "evaluations", "sources", "ocr", "tickets",
                         "costs", "performance", "system", "security"):
                pg.click(f"#t-{name}")
                pg.wait_for_selector(f"#d-{name}:not([aria-busy])", state="attached")
                pg.wait_for_timeout(150)
                pg.locator("main").screenshot(path=out / f"dashboard-{name}.png")

            pg = page(1280, 900, dark=True)
            pg.goto(url + "/admin")
            pg.fill("#key", KEY)
            pg.dispatch_event("#key", "change")
            pg.wait_for_selector("#role:has-text('admin')")
            pg.click("#t-costs")
            pg.wait_for_selector("#d-costs:not([aria-busy])", state="attached")
            pg.locator("main").screenshot(path=out / "dashboard-costs-dark.png")

            for path, name in (("/request", "request-form"), ("/privacy", "privacy")):
                pg = page(1000, 900)
                pg.goto(url + path)
                pg.screenshot(path=out / f"{name}.png", full_page=True)
            browser.close()
        server.should_exit = True
    from PIL import Image
    for png in out.glob("*.png"):  # 128-colour palette keeps the repository small
        Image.open(png).convert("RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT).save(png, optimize=True)
    print("\n".join(sorted(str(p) for p in out.glob("*.png"))))


if __name__ == "__main__":
    main()
