"""Plan 7 phase D: evaluator-critic loop and the semantic cache's safety rules."""
import time

import pytest

from agentkit import critic
from agentkit.chat import LibraryChat
from agentkit.llm import FakeLLM, Grounded
from agentkit.metrics import METRICS
from agentkit.rag import Index


class Drafting(FakeLLM):
    """Live-mode stand-in: first draft from `drafts`, then the extractive answer; scripted critic replies."""
    live = True

    def __init__(self, drafts=(), review='{"faithful": 5, "policy": 5, "complete": 5}'):
        super().__init__()
        self.drafts, self.review, self.answers = list(drafts), review, 0

    def answer(self, system, question, sources):
        self.answers += 1
        g = super().answer(system, question, sources)
        if self.drafts:
            return Grounded(self.drafts.pop(0).format(n=g.cited[0]), g.cited, g.quotes)
        return g

    def complete(self, system, user, *, fast=False, max_tokens=None):
        if system.startswith("You review"):
            return self.review
        if system.startswith("You check search results"):
            return "1,2,3"
        return super().complete(system, user, fast=fast, max_tokens=max_tokens)


def steps(ans):
    return {s["step"]: s for s in ans.trace}


def test_mechanical_checks():
    from agentkit.rag import Chunk
    c = Chunk(id="a", text="Borrow › Limits\nAlumni can borrow up to 5 books for 14 days.", title="Borrow", section="",
              source="https://library.aucegypt.edu/b", page=1, lang="en", method="text")
    ok = "Alumni can borrow up to 5 books for 14 days [1]."
    assert critic.check(ok, {1: ["Alumni can borrow up to 5 books for 14 days."]}, [(1, c)]) == []
    bad = critic.check("Alumni can borrow 10 books, see https://evil.example [1].", {1: ["made up quote"]}, [(1, c)])
    assert any("number 10" in i for i in bad) and any("link" in i for i in bad) and any("quote" in i for i in bad)
    assert critic.check("الخريجين يستعيروا ٥ كتب لمدة ١٤ يوم [1].", {}, [(1, c)]) == []  # Arabic-Indic digits


def test_fabricated_number_is_revised(seed_index):
    llm = Drafting(drafts=["Alumni can borrow up to 50 books [{n}]."])
    ans = LibraryChat(seed_index, llm, cache=None).ask("How many books can alumni borrow?")
    assert steps(ans)["critic"]["critic"] == "revised" and "50" not in ans.text and "5 books" in ans.text
    assert ans.mode == "answer"


def test_failed_revision_becomes_handoff_with_sources(seed_index):
    llm = Drafting(drafts=["Alumni can borrow up to 50 books [{n}].", "Still 60 books [{n}]."])
    ans = LibraryChat(seed_index, llm, cache=None).ask("How many books can alumni borrow?")
    assert ans.mode == "handoff" and "couldn't confirm" in ans.text and ans.sources
    assert "50" not in ans.text and "60" not in ans.text


def test_model_critic_low_score_triggers_fix(seed_index):
    llm = Drafting(review='{"faithful": 2, "policy": 5, "complete": 5, "issues": ["overstates"], "fix": "x"}')
    ans = LibraryChat(seed_index, llm, cache=None).ask("How many books can alumni borrow?")
    assert steps(ans)["critic"]["critic"] in ("revised", "handoff") and llm.answers == 2


def test_correct_answer_passes_and_offline_skips_model(seed_index):
    ans = LibraryChat(seed_index, FakeLLM(), cache=None).ask("How many books can alumni borrow?")
    assert steps(ans)["critic"]["critic"] == "pass" and ans.mode == "answer"


def test_research_answers_are_held_while_checking(seed_index):
    chat = LibraryChat(seed_index, Drafting(drafts=["The RBSCL holds 900 manuscripts [{n}]."]), cache=None)
    events = list(chat.ask_stream("What are the rare books library collection strengths?"))
    kinds = [k for k, _ in events]
    assert kinds[0] == "status" and kinds.count("delta") == 1  # nothing unchecked was streamed
    assert "900" not in events[-1][1].text


@pytest.fixture
def sem(seed_index, monkeypatch):
    monkeypatch.setenv("AGENTKIT_SEMCACHE", "1")
    return LibraryChat(Index(seed_index.chunks), FakeLLM(), cache=None)


def test_paraphrase_hits(sem):
    first = sem.ask("How many books can alumni borrow?")
    hit = sem.ask("how many books are alumni allowed to borrow")
    assert hit.trace[0]["step"] == "semantic_cache" and hit.text == first.text and hit.id != first.id


def test_unsafe_neighbours_miss(sem):
    sem.ask("How many books can alumni borrow?")
    other = sem.ask("How many books can undergraduates borrow?")
    assert other.trace[0]["step"] != "semantic_cache" and "20" in other.text  # different audience
    sem.ask("Can I renew a book 2 times?")
    assert sem.ask("Can I renew a book 3 times?").trace[0]["step"] != "semantic_cache"  # different number
    sem.ask("Where is the rare books library?")
    assert sem.ask("When is the rare books library open?").trace[0]["step"] != "semantic_cache"  # where ≠ when
    sem.ask("ممكن الخريجين يستعيروا كتب؟")
    assert sem.ask("Can alumni borrow books?").trace[0]["step"] != "semantic_cache"  # language differs


def test_live_and_index_changes_never_hit(sem):
    from agentkit.rag import Chunk
    sem.ask("How many books can alumni borrow?")
    sem.index.add([Chunk(id="new", text="Notice › x\nNew page.", title="Notice", section="", source="n.md", page=1,
                         lang="en", method="text")])
    assert sem.ask("how many books are alumni allowed to borrow").trace[0]["step"] != "semantic_cache"


def test_hours_answers_are_not_stored(sem):
    from agentkit.semcache import cacheable
    from agentkit.chat import Answer
    from agentkit.rag import Chunk
    live = Chunk(id="h", text="Hours › Today\nOpen until 9 pm.", title="Hours", section="", source="libcal", page=1,
                 lang="en", method="live-hours")
    assert not cacheable(Answer("Open until 9 pm [1].", "auc-library-concierge", "en", hits=[(1.0, live)]))


def test_semantic_hit_latency(sem):
    for q in ["How many books can alumni borrow?", "Can I renew online?", "Where is the rare books library?",
              "Who can borrow books?", "Where are AUC theses published?"]:
        sem.ask(q)
    times = []
    for _ in range(50):
        t0 = time.perf_counter()
        sem.ask("how many books are alumni allowed to borrow")
        times.append((time.perf_counter() - t0) * 1000)
    assert sorted(times)[int(0.95 * len(times))] < 15  # ms, p95, trigram vectors, in-process
    assert METRICS.value("agentkit_semantic_cache_hits_total") >= 50


class FakeRedis:
    def __init__(self):
        self.data = {}

    def rpush(self, k, v):
        self.data.setdefault(k, []).append(v)

    def ltrim(self, k, a, b):
        self.data[k] = self.data[k][a:] if b == -1 else self.data[k][a:b + 1]

    def expire(self, k, s):
        pass

    def lrange(self, k, a, b):
        return list(self.data.get(k, []))


def test_redis_store_round_trips_answers(sem):
    shared = FakeRedis()
    sem.semcache.redis = shared
    first = sem.ask("How many books can alumni borrow?")
    other_worker = LibraryChat(sem.index, FakeLLM(), cache=None)
    other_worker.semcache.redis = shared  # a second process sharing the same Redis
    hit = other_worker.ask("how many books are alumni allowed to borrow")
    assert hit.trace[0]["step"] == "semantic_cache" and hit.text == first.text
    assert hit.sources and hit.sources[0][1].title == first.sources[0][1].title
