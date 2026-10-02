"""Real-browser accessibility check: axe-core (WCAG 2.x A/AA rules) in Chromium, in English and Arabic."""
import socket
import threading
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
axe_mod = pytest.importorskip("axe_playwright_python.sync_playwright")
CHROMIUM = next((p for p in ("/opt/pw-browsers/chromium",) if Path(p).exists()), None)


@pytest.fixture(scope="module")
def base_url(seed_index):
    import uvicorn
    from agentkit.api import create_app
    from agentkit.chat import LibraryChat
    from agentkit.llm import FakeLLM
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(LibraryChat(seed_index, FakeLLM())), port=port,
                                           log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(scope="module")
def page():
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Chromium available: {e}")
        yield browser.new_page()
        browser.close()


def _violations(page):
    results = axe_mod.Axe().run(page, options={"runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]}})
    return [(v["id"], v["impact"]) for v in results.response["violations"]]


@pytest.mark.parametrize("path", ["/", "/admin"])
def test_pages_have_no_wcag_violations(page, base_url, path):
    page.goto(base_url + path)
    assert _violations(page) == []


def test_chat_flow_arabic_and_keyboard(page, base_url):
    page.goto(base_url + "/")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip"
    page.click("#lang")
    assert page.get_attribute("html", "dir") == "rtl" and page.get_attribute("html", "lang") == "ar"
    page.fill("#q", "ممكن الخريجين يستعيروا كتب؟")
    page.press("#q", "Enter")
    page.wait_for_selector(".msg[aria-busy='false'] ol.src", timeout=10000)
    assert "14" in page.inner_text("#log") and page.inner_text("#status") == "الإجابة جاهزة."
    assert page.evaluate("document.activeElement.id") == "q"  # focus returns to the input
    assert _violations(page) == []  # also with a rendered answer, in RTL
