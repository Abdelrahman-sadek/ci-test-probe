import asyncio
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import lint_agents
from agentkit.agents import load_all
from agentkit.evals import run_golden, run_smoke
from agentkit.llm import FakeLLM
from agentkit.web import make_handler

from conftest import ROOT


def test_lint_passes_on_repo():
    errors, count = lint_agents.lint(ROOT)
    assert not errors and count == len(load_all())


def test_lint_catches_bad_agent(tmp_path):
    d = tmp_path / "plugins/x/agents"
    d.mkdir(parents=True)
    (d / "bad-agent.md").write_text("---\nname: other\ndescription: does things\nmodel: gpt-4\ncolor: teal\n---\nhi\n")
    errors, _ = lint_agents.lint(tmp_path)
    text = "\n".join(errors)
    for needle in ("must equal filename", "must start with 'Use '", "model 'gpt-4'", "color 'teal'", "## Rules"):
        assert needle in text


def test_golden_eval_meets_ci_thresholds(chat):
    rep = run_golden(chat)
    failed = [r for r in rep["results"] if not r["pass"]]
    assert not failed, failed
    assert rep["recall_at_k"] >= 0.9 and rep["mrr"] >= 0.9


def test_smoke_tests_cover_every_agent():
    rep = run_smoke(FakeLLM())
    assert rep["passed"] == rep["total"] == len(load_all()) and not rep["missing"]


@pytest.fixture
def server(chat):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(chat))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _post(url, payload: bytes):
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=5)


def test_web_ask_returns_cited_answer(server):
    with _post(server + "/api/ask", json.dumps({"question": "Can alumni borrow books?"}).encode()) as r:
        data = json.load(r)
        assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert data["mode"] == "answer" and data["sources"][0]["url"].startswith("https://")


def test_web_rejects_oversized_body(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(server + "/api/ask", b"{" + b" " * 20000 + b"}")
    assert e.value.code == 413


def test_web_page_and_js_served(server):
    html = urllib.request.urlopen(server + "/").read().decode()
    js = urllib.request.urlopen(server + "/app.js").read().decode()
    assert "AUC Library Assistant" in html and "textContent" in js and "innerHTML" not in js


def test_mcp_server_exposes_tools_and_agent_prompts():
    pytest.importorskip("mcp")
    from agentkit.mcp_server import build_server
    s = build_server(str(ROOT / "data/missing-index.json"))
    tools = {t.name for t in asyncio.run(s.list_tools())}
    prompts = {p.name for p in asyncio.run(s.list_prompts())}
    assert {"search_library", "ask_library", "list_agents"} <= tools and set(load_all()) == prompts
