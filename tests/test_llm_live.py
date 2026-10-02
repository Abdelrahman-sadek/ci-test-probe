"""Live-mode request shapes and response parsing, verified against a mocked Anthropic client (no API key)."""
from types import SimpleNamespace as NS

from agentkit import llm as L


class FakeAPI:
    def __init__(self, response):
        self.calls, self.response = [], response
        self.messages = NS(create=self._create("messages"))
        self.beta = NS(messages=NS(create=self._create("beta")))

    def _create(self, ns):
        def create(**kw):
            self.calls.append((ns, kw))
            return self.response
        return create


def text(t, cites=()):
    return NS(type="text", text=t, citations=[NS(type="search_result_location", search_result_index=i, cited_text=q)
                                              for i, q in cites])


SOURCES = [{"title": "Borrow", "source": "https://x/borrow", "blocks": ["Borrow › Limits", "Alumni: 5 books."]},
           {"title": "Hours", "source": "https://x/hours", "blocks": ["Hours", "Check the website."]}]


def test_answer_uses_search_result_blocks_with_citations_and_fallbacks():
    api = FakeAPI(NS(stop_reason="end_turn", content=[NS(type="thinking", thinking=""),
                                                      text("Alumni can borrow 5 books.", [(0, "Alumni: 5 books.")])]))
    g = L.AnthropicLLM(api).answer("system", "Can alumni borrow?", SOURCES)
    ns, kw = api.calls[0]
    blocks = kw["messages"][0]["content"]
    assert ns == "beta" and kw["fallbacks"] == "default" and kw["betas"] == [L.FALLBACK_BETA]
    assert kw["model"] == L.MODEL_SMART and kw["output_config"] == {"effort": L.EFFORT}
    assert blocks[0]["type"] == "search_result" and blocks[0]["citations"] == {"enabled": True}
    assert all(b["citations"]["enabled"] for b in blocks[:2]) and blocks[-1]["type"] == "text"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert g.cited == [1] and g.quotes[1] == ["Alumni: 5 books."] and g.text.endswith("[1]")


def test_fast_model_has_no_effort_or_fallbacks():
    api = FakeAPI(NS(stop_reason="end_turn", content=[text("keywords")]))
    L.AnthropicLLM(api).complete("sys", "q", fast=True)
    ns, kw = api.calls[0]
    assert ns == "messages" and kw["model"] == L.MODEL_FAST and "output_config" not in kw and "fallbacks" not in kw


def test_refusal_is_handled():
    api = FakeAPI(NS(stop_reason="refusal", content=[]))
    g = L.AnthropicLLM(api).answer("s", "q", SOURCES)
    assert g.text == L.REFUSAL_TEXT and g.cited == []


def test_contextualize_caches_document_and_ocr_sends_image():
    api = FakeAPI(NS(stop_reason="end_turn", content=[text("ctx")]))
    client = L.AnthropicLLM(api)
    assert client.contextualize("whole document", "a chunk") == "ctx"
    _, kw = api.calls[0]
    assert "whole document" in kw["system"][0]["text"] and "a chunk" in kw["messages"][0]["content"]
    client.ocr_image(b"\x89PNG")
    _, kw = api.calls[1]
    assert kw["messages"][0]["content"][0]["type"] == "image" and kw["max_tokens"] >= 8000
