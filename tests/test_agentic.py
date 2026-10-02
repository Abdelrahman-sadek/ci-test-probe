"""Agentic retrieval (after jamwithai/production-agentic-rag-course): retrieve → grade → rewrite → retry,
per-request traces, and model-free search."""
from agentkit.chat import LibraryChat
from agentkit.llm import FakeLLM
from test_features import app  # noqa: F401  (fixture reuse)


def steps(ans):
    return [s["step"] for s in ans.trace]


def test_misspelled_question_is_rewritten_and_answered(seed_index, monkeypatch):
    chat = LibraryChat(seed_index, FakeLLM(), cache=None)
    ans = chat.ask("dissertatoins onlin")
    assert ans.mode == "answer" and "Knowledge Fountain" in ans.text
    assert steps(ans) == ["route", "retrieve", "rewrite", "retrieve", "generate"]
    monkeypatch.setenv("AGENTKIT_MAX_RETRIEVAL_ATTEMPTS", "1")
    assert chat.ask("dissertatoins onlin").mode == "handoff"  # without the retry it was a dead end


def test_out_of_scope_still_hands_off_after_retry(seed_index):
    ans = LibraryChat(seed_index, FakeLLM(), cache=None).ask("Who won the football match yesterday?")
    assert ans.mode == "handoff" and steps(ans).count("retrieve") <= 2


class Grader(FakeLLM):
    live = True

    def __init__(self, reply):
        super().__init__()
        self.reply = reply

    def complete(self, system, user, *, fast=False, max_tokens=None):
        self.calls.append((system, user))
        if system.startswith("You check search results"):
            return self.reply
        if system.startswith("Rewrite the library question"):
            return "zzzz unrelated"
        return super().complete(system, user, fast=fast, max_tokens=max_tokens)


def test_grader_drops_irrelevant_chunks(seed_index):
    llm = Grader("1")
    ans = LibraryChat(seed_index, llm, cache=None).ask("How many books can undergraduates borrow?")
    grade = next(s for s in ans.trace if s["step"] == "grade")
    assert grade["kept"] == 1 and len(ans.hits) == 1
    assert "@" not in llm.calls[0][1]  # grading sees the redacted question


def test_grader_none_triggers_rewrite_then_handoff(seed_index):
    ans = LibraryChat(seed_index, Grader("NONE"), cache=None).ask("How many books can undergraduates borrow?")
    assert "rewrite" in steps(ans) and ans.mode == "handoff"


def test_trace_has_no_question_text(seed_index):
    ans = LibraryChat(seed_index, FakeLLM(), cache=None).ask("my email is a@b.com, can alumni borrow books?")
    assert "a@b.com" not in str(ans.to_dict()["trace"])


def test_search_endpoint_without_model(app):  # noqa: F811
    client = app[0]
    r = client.get("/api/search", params={"q": "rare books external visitors passport", "k": 3}).json()
    assert r["results"] and "Visit and Access" in r["results"][0]["title"]
    assert client.get("/api/search", params={"q": ""}).status_code == 422
