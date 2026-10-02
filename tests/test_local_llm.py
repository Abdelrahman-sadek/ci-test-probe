"""Self-hosted model backend (OpenAI-compatible): citations grounded, invalid ones dropped, pipeline unchanged."""
from agentkit.chat import LibraryChat
import json

import pytest

from agentkit.local_llm import Endpoint, LocalLLM, ground, load_endpoints
from agentkit.llm import NO_ANSWER


def server(reply):
    calls = []

    def post(url, payload, ep=None):
        calls.append((url, payload))
        sys = payload["messages"][0]["content"] if isinstance(payload["messages"][0]["content"], str) else ""
        if sys.startswith("You check search results"):
            content = "1"
        elif sys.startswith("You review"):
            content = '{"faithful": 5, "policy": 5, "complete": 5}'
        else:
            content = reply
        return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    return post, calls


def test_ground_keeps_real_citations_and_quotes():
    sources = [{"title": "Borrow", "blocks": ["Borrow", "Alumni can borrow up to 5 books for 14 days.", "Renew online."]}]
    g = ground("Alumni can borrow up to 5 books for 14 days [1]. Also see [7].", sources)
    assert g.cited == [1] and "[7]" not in g.text and g.quotes[1] == ["Alumni can borrow up to 5 books for 14 days."]
    assert ground(NO_ANSWER, sources).cited == []


def test_local_model_runs_the_whole_pipeline(seed_index):
    post, calls = server("Alumni can borrow up to 5 books for 14 days [1].")
    llm = LocalLLM([Endpoint("main", "http://gpu:8000/v1", "qwen-14b")], post=post)
    ans = LibraryChat(seed_index, llm, cache=None).ask("How many books can alumni borrow?")
    assert ans.mode == "answer" and "5 books" in ans.text and ans.sources
    assert calls[-1][0] == "http://gpu:8000/v1/chat/completions" and calls[-1][1]["model"] == "qwen-14b"
    assert "[1]" in calls[-1][1]["messages"][1]["content"]  # numbered search results were sent


def test_local_fabrication_is_caught_by_the_critic(seed_index):
    post, _ = server("Alumni can borrow up to 50 books [1].")
    ans = LibraryChat(seed_index, LocalLLM([Endpoint("m", "http://g/v1", "m")], post=post), cache=None).ask(
        "How many books can alumni borrow?")
    assert "50" not in ans.text  # revised (still 50 here) → handoff with sources


def test_get_llm_selects_local(monkeypatch):
    from agentkit.llm import ResilientLLM, get_llm
    monkeypatch.setenv("AGENTKIT_LLM", "local")
    llm = get_llm()
    assert isinstance(llm, ResilientLLM) and isinstance(llm.inner, LocalLLM)


POOL = [Endpoint("main", "http://gpu1/v1", "qwen-32b", ["answer"], priority=1),
        Endpoint("arabic", "http://gpu2/v1", "arabic-7b", ["answer"], langs=["ar", "arabizi"], priority=2),
        Endpoint("fast", "http://gpu2:8001/v1", "qwen-7b", ["fast"]),
        Endpoint("vision", "http://gpu2:8002/v1", "qwen-vl", ["vision"]),
        Endpoint("backup", "http://cpu/v1", "small", ["answer", "fast"], priority=9)]


def recorder(down=()):
    used = []

    def post(url, payload, ep):
        if ep.name in down:
            raise ConnectionError("server down")
        used.append(ep.name)
        return {"choices": [{"message": {"content": "ok [1]"}}], "usage": {}}
    return post, used


def test_pool_routes_by_role_and_language():
    post, used = recorder()
    llm = LocalLLM(POOL, post=post)
    src = [{"title": "t", "blocks": ["t", "ok"]}]
    llm.answer("s", "How many books can alumni borrow?", src)
    llm.answer("s", "ممكن الخريجين يستعيروا كتب؟", src)
    llm.complete("grade", "x", fast=True)
    llm.ocr_image(b"png")
    assert used == ["main", "arabic", "fast", "vision"]


def test_pool_fails_over_and_reports():
    post, used = recorder(down={"main", "fast"})
    llm = LocalLLM(POOL, post=post)
    llm.answer("s", "Can alumni borrow?", [{"title": "t", "blocks": ["t", "ok"]}])
    llm.complete("rewrite", "x", fast=True)
    assert used == ["arabic", "backup"]  # next answer server, then the backup for the fast role
    assert {e["name"]: e["failovers"] for e in llm.status()}["main"] >= 1
    post, _ = recorder(down={e.name for e in POOL})
    with pytest.raises(ConnectionError):
        LocalLLM(POOL, post=post).complete("s", "x")


def test_pool_config_file(tmp_path):
    cfg = tmp_path / "m.json"
    cfg.write_text(json.dumps({"models": [{"name": "a", "url": "http://x/v1", "model": "m", "roles": ["answer"]}]}))
    assert load_endpoints(cfg)[0].model == "m"
    cfg.write_text(json.dumps({"models": [{"name": "a", "url": "http://x/v1", "model": "m", "roles": ["chat"]}]}))
    with pytest.raises(ValueError):
        load_endpoints(cfg)
    from agentkit import ROOT
    assert len(load_endpoints(ROOT / "config/local-models.example.json")) == 5
