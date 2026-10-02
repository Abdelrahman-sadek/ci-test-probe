"""Every dashboard tab, filled with data, passes axe (WCAG 2.2 AA) in Chromium and works from the keyboard."""
import socket
import threading
import time

import pytest

from test_a11y import CHROMIUM, _violations, axe_mod, pw  # noqa: F401  (skips when playwright is missing)

KEY = "k" * 32


@pytest.fixture(scope="module")
def dash_url(seed_index, tmp_path_factory):
    import os
    import uvicorn
    from agentkit.api import create_app
    from agentkit.appdb import AppDB
    from agentkit.budget import attach
    from agentkit.chat import LibraryChat
    from agentkit.llm import FakeLLM
    from agentkit.rag import Index
    os.environ["AGENTKIT_ADMIN_KEY"] = KEY
    db = AppDB(tmp_path_factory.mktemp("dash") / "app.db")
    chat = LibraryChat(Index(seed_index.chunks), FakeLLM(), appdb=db, cache=None)
    chat.budget = attach(chat.llm, db)
    chat.budget.caps["day"] = 5.0
    for q in ["How many books can alumni borrow?", "ممكن اجدد الكتاب بالتليفون؟", "Who won the match?",
              "fines for lost books", "lost book fine", "how much is the fine for a lost book"]:
        chat.ask(q, user=f"u{len(q)}@aucegypt.edu")
    db.add_usage("claude-opus-5-5", {"input": 1000, "output": 200}, 0.012, {"plugin": "auc-library", "purpose": "answer",
                                                                          "agent": "auc-library-concierge"})
    db.add_ticket("handoff", {"question": "Need a 1950 newspaper"}, "Reference desk")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(chat), port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    os.environ.pop("AGENTKIT_ADMIN_KEY", None)


@pytest.fixture(scope="module")
def page():
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Chromium available: {e}")
        yield browser.new_page()
        browser.close()


def test_every_tab_renders_and_passes_axe(page, dash_url):
    page.goto(dash_url + "/admin")
    page.fill("#key", KEY)
    page.dispatch_event("#key", "change")
    page.wait_for_selector("#role:has-text('admin')")
    tabs = page.eval_on_selector_all('[role="tab"]', "els => els.map(e => e.dataset.tab)")
    assert len(tabs) == 14
    for name in tabs:
        page.click(f"#t-{name}")
        if name == "conversations":
            page.wait_for_selector("#f-reason")
            page.fill("#f-reason", "accessibility test")
            page.click("#d-conversations button")
        page.wait_for_selector(f"#d-{name}:not([aria-busy])", state="attached")
        text = page.inner_text(f"#p-{name}")
        assert "Not available" not in text, (name, text[:200])
        assert _violations(page) == [], (name, _violations(page))


def test_tabs_work_from_the_keyboard(page, dash_url):
    page.goto(dash_url + "/admin")
    page.fill("#key", KEY)
    page.dispatch_event("#key", "change")
    page.wait_for_selector("#role:has-text('admin')")
    page.click("#t-today")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.id") == "t-trends"
    assert page.get_attribute("#p-trends", "hidden") is None
    page.keyboard.press("End")
    assert page.evaluate("document.activeElement.id") == "t-security"


def test_screenshots_for_review(page, dash_url, tmp_path):
    import os
    out = os.getenv("AGENTKIT_SHOTS")
    if not out:
        pytest.skip("set AGENTKIT_SHOTS=<dir> to save screenshots")
    page.set_viewport_size({"width": 1200, "height": 900})
    page.goto(dash_url + "/admin")
    page.fill("#key", KEY)
    page.dispatch_event("#key", "change")
    page.wait_for_selector("#role:has-text('admin')")
    for name in ("today", "costs", "gaps", "system"):
        page.click(f"#t-{name}")
        page.wait_for_selector(f"#d-{name}:not([aria-busy])", state="attached")
        page.screenshot(path=f"{out}/{name}.png", full_page=True)
