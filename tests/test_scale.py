from types import SimpleNamespace as NS

import pytest

from agentkit import llm as L
from agentkit.cache import AnswerCache
from agentkit.chat import LibraryChat
from agentkit.evals import run_golden
from agentkit.jobs import JobQueue
from agentkit.llm import FakeLLM
from agentkit.metrics import Metrics
from agentkit.rag import Index, approve, ingest
from agentkit.store_sqlite import SqliteIndex

from conftest import SEED


@pytest.fixture(params=["json", "sqlite"])
def backend(request, tmp_path):
    return Index() if request.param == "json" else SqliteIndex(tmp_path / "i.db")


def _page(path, body, **meta):
    fm = "\n".join(f"{k}: {v}" for k, v in {"title": path.stem, **meta}.items())
    path.write_text(f"---\n{fm}\n---\n# {path.stem}\n\n## Info\n{body}\n", encoding="utf-8")
    return path


def test_both_backends_pass_golden_set(backend):
    idx, _ = ingest([SEED], FakeLLM(), backend)
    rep = run_golden(LibraryChat(idx, FakeLLM()))
    assert rep["passed"] == rep["total"] and rep["recall_at_k"] == 1.0


def test_incremental_update_replaces_old_chunks(backend, tmp_path):
    p = _page(tmp_path / "hours.md", "The reading room opens at nine every weekday morning.")
    idx, _ = ingest([p], FakeLLM(), backend)
    _, rep = ingest([p], FakeLLM(), idx)
    assert rep[0]["status"] == "unchanged"
    _page(p, "The reading room now opens at eight every weekday morning.")
    _, rep = ingest([p], FakeLLM(), idx)
    texts = [c.text for _, c in idx.search("reading room opens", 5)]
    assert rep[0]["status"] == "updated" and len(texts) == 1 and "eight" in texts[0]


def test_review_holds_changed_sources_until_approved(backend, tmp_path):
    p = _page(tmp_path / "fines.md", "Overdue fines are five pounds per day for every book.")
    idx, _ = ingest([p], FakeLLM(), backend, review=True)
    assert idx.search("overdue fines", 3)  # first ingest of a new source is trusted
    _page(p, "Overdue fines are now fifty pounds per day for every book.")
    _, rep = ingest([p], FakeLLM(), idx, review=True)
    assert rep[0]["status"] == "pending-review" and idx.search("overdue fines", 3) == []
    assert approve(idx) == [str(p)] and "fifty" in idx.search("overdue fines", 3)[0][1].text


def test_access_levels_on_both_backends(backend, tmp_path):
    p = _page(tmp_path / "staff.md", "Staff rota for the circulation desk changes every Sunday.", access="staff")
    idx, _ = ingest([p], FakeLLM(), backend)
    assert idx.search("staff rota circulation") == []
    assert idx.search("staff rota circulation", access=("public", "staff"))


class StubEmbedder:
    name = "stub"

    def embed(self, texts):
        return [[1.0, 0.0] if "rare" in t.lower() else [0.0, 1.0] for t in texts]


def test_qdrant_vectors_in_sqlite_index(tmp_path):
    pytest.importorskip("qdrant_client")
    from agentkit.vector import QdrantVectors
    idx = SqliteIndex(tmp_path / "v.db", embedder=StubEmbedder(), vectors=QdrantVectors(2, ":memory:"))
    ingest([_page(tmp_path / "a.md", "Rare manuscripts are kept upstairs in the reading room."),
            _page(tmp_path / "b.md", "Printing costs one pound per page at the main desk.")], FakeLLM(), idx)
    assert idx._rank_dense("unusual old items rare", ("public",), 5)[0] == idx._rank_bm25("manuscripts", ("public",), 5)[0]


def test_cache_hits_and_invalidates_on_index_change(seed_index, tmp_path):
    idx = Index(seed_index.chunks)
    chat = LibraryChat(idx, FakeLLM(), cache=AnswerCache())
    a1 = chat.ask("Can alumni borrow books?")
    calls = len(chat.llm.calls)
    assert chat.ask("can alumni borrow books") is a1 and len(chat.llm.calls) == calls  # served from cache
    assert chat.ask("Can alumni borrow books?", access=("public", "staff")) is not a1  # per access level
    ingest([_page(tmp_path / "new.md", "A brand new page about printing services at the library.")], FakeLLM(), idx)
    assert chat.ask("Can alumni borrow books?") is not a1  # index version changed → cache miss


def test_metrics_render_and_cost():
    m = Metrics()
    m.inc("agentkit_requests_total", mode="answer")
    m.observe("agentkit_stage_seconds", 0.02, stage="retrieve")
    m.record_usage("claude-opus-5-5", NS(input_tokens=1_000_000, output_tokens=100_000,
                                         cache_read_input_tokens=0, cache_creation_input_tokens=0))
    text = m.render()
    assert 'agentkit_requests_total{mode="answer"} 1' in text and "agentkit_stage_seconds_bucket" in text
    assert abs(m.value("agentkit_llm_cost_usd_total") - 6.0) < 1e-9  # $4 input + $2 output


def test_streaming_fake_and_chat(chat):
    events = list(chat.ask_stream("Can alumni borrow books?"))
    assert events[-1][0] == "done" and events[-1][1].mode == "answer"
    assert "".join(d for k, d in events[:-1]).strip() == events[-1][1].text


class FakeStream:
    def __init__(self, final):
        self.final, self.text_stream = final, iter(["Alumni ", "can borrow 5."])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.final


def test_live_streaming_parses_citations():
    final = NS(stop_reason="end_turn", usage=None, content=[NS(type="text", text="Alumni can borrow 5.", citations=[
        NS(type="search_result_location", search_result_index=0, cited_text="Alumni: 5 books.")])])
    calls = []
    api = NS(stream=lambda **kw: calls.append(kw) or FakeStream(final))
    client = NS(beta=NS(messages=api), messages=api)
    out = list(L.AnthropicLLM(client).stream_answer("s", "q", [{"title": "B", "source": "u", "blocks": ["h", "x"]}]))
    assert [d for k, d in out if k == "delta"] == ["Alumni ", "can borrow 5."]
    assert out[-1][1].cited == [1] and calls[0]["fallbacks"] == "default"


def test_batch_contextualize_uses_batches_api():
    created = {}
    results = [NS(custom_id="c1", result=NS(type="succeeded", message=NS(usage=None, content=[NS(type="text", text="ctx B")]))),
               NS(custom_id="c0", result=NS(type="succeeded", message=NS(usage=None, content=[NS(type="text", text="ctx A")])))]
    batches = NS(create=lambda requests: created.setdefault("r", requests) and NS(id="b1"),
                 retrieve=lambda bid: NS(processing_status="ended"), results=lambda bid: iter(results))
    client = NS(messages=NS(batches=batches), beta=None)
    out = L.AnthropicLLM(client).contextualize_batch([("doc", "chunk a"), ("doc", "chunk b")], poll_seconds=0)
    assert out == ["ctx A", "ctx B"]  # re-ordered by custom_id
    params = created["r"][0]["params"]
    assert params["model"] == L.MODEL_FAST and "fallbacks" not in params and "output_config" not in params


def test_job_queue_ingests_in_background(tmp_path):
    idx = Index()
    jobs = JobQueue(idx, FakeLLM(), tmp_path / "idx.json")
    jid = jobs.submit([str(_page(tmp_path / "p.md", "Study rooms can be booked for two hours per day online."))])
    job = jobs.wait(jid)
    assert job["status"] == "done" and idx.search("study rooms booked") and (tmp_path / "idx.json").exists()
