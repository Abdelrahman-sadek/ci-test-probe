"""Plan 7 phase E: OAI-PMH thesis harvesting, metadata filters on both back ends, thesis questions."""
import json
from pathlib import Path

import pytest

from agentkit.harvest import HarvestError, harvest, record_meta, restricted
from agentkit.llm import FakeLLM
from agentkit.rag import Index, ingest, meta_match

FIX = Path("samples/fixtures/oai")
BASE = "https://fount.aucegypt.edu/do/oai/"


def fetcher(calls=None, fail_on_page2=False):
    pages = {"1": (FIX / "page1.xml").read_bytes(), "2": (FIX / "page2.xml").read_bytes()}

    def fetch(url):
        if calls is not None:
            calls.append(url)
        if "viewcontent" in url:
            return b"%PDF-1.4 not really"  # tiny fake PDF header; content not parsed here
        if "resumptionToken=page2token" in url:
            if fail_on_page2:
                raise ConnectionError("network dropped")
            return pages["2"]
        return pages["1"]
    return fetch


def test_harvest_filters_records_and_resumes(tmp_path):
    with pytest.raises(ConnectionError):
        harvest(BASE, tmp_path, "publication:etds", fetch=fetcher(fail_on_page2=True))
    assert json.loads((tmp_path / "state.json").read_text())["token"] == "page2token"  # resumable
    calls = []
    rep = harvest(BASE, tmp_path, fetch=fetcher(calls))
    assert "resumptionToken=page2token" in calls[0]  # continued where it stopped
    assert rep["skipped_restricted"] == 1 and rep["not_thesis"] == 1 and rep["deleted"] == 1
    assert len(list(tmp_path.glob("*.md"))) == 12
    side = json.loads((tmp_path / "etds-1001.md.meta.json").read_text())
    assert side["biblio"]["advisor"] == "Ibrahim, Sara" and side["biblio"]["department"] == "Economics"
    assert not list(tmp_path.glob("etds-1013*"))  # embargoed: never written
    assert json.loads((tmp_path / "state.json").read_text())["from"]  # next run is incremental


def test_harvest_refuses_unsafe_sources_and_xml(monkeypatch):
    monkeypatch.setenv("AGENTKIT_ALLOWED_DOMAINS", "aucegypt.edu")
    with pytest.raises(HarvestError):
        harvest("http://fount.aucegypt.edu/do/oai/", "/tmp/x", fetch=fetcher())
    with pytest.raises(HarvestError):
        harvest("https://evil.example/oai", "/tmp/x", fetch=fetcher())
    with pytest.raises(HarvestError):
        harvest(BASE, "/tmp/x", fetch=lambda u: b'<!DOCTYPE x [<!ENTITY a "b">]><x/>')


def test_restricted_rules():
    assert restricted({"rights": "Embargoed until 2030", "dates": []})
    assert restricted({"rights": "", "dates": ["2099-01-01"]}, today="2026-10-02")
    assert not restricted({"rights": "Open access", "dates": ["2019-06-01T00:00:00"]}, today="2026-10-02")


def test_meta_match():
    m = {"type": "thesis", "department": "Political Science", "advisor": "Ibrahim, Sara", "year": "2015"}
    assert meta_match(m, {"type": "thesis", "advisor": "Sara Ibrahim", "year_to": "2016"})
    assert not meta_match(m, {"year_from": "2016"}) and not meta_match(m, {"department": "Economics"})


@pytest.fixture(scope="module")
def thesis_indexes(tmp_path_factory):
    d = tmp_path_factory.mktemp("th")
    harvest(BASE, d / "th", fetch=fetcher())
    out = {}
    from agentkit.store_sqlite import SqliteIndex
    for name, idx in (("json", Index()), ("sqlite", SqliteIndex(d / "t.db"))):
        ingest(["knowledge/auc-library/pages", str(d / "th")], FakeLLM(), idx, review=False)
        out[name] = idx
    return out


@pytest.mark.parametrize("backend", ["json", "sqlite"])
def test_filters_on_both_backends(thesis_indexes, backend):
    idx = thesis_indexes[backend]
    econ = idx.search("policy", 10, [], ("public",), filters={"type": "thesis", "department": "Economics"})
    assert econ and all(c.meta["department"] == "Economics" for _, c in econ)
    recent = idx.search("thesis", 20, [], ("public",), filters={"type": "thesis", "year_from": 2021})
    assert recent and all(int(c.meta["year"]) >= 2021 for _, c in recent)
    adv = idx.search("thesis", 20, [], ("public",), filters={"type": "thesis", "advisor": "Laila Nassar"})
    assert {c.title for _, c in adv} == {"Informal Housing and Urban Planning in Greater Cairo",
                                         "Refugee Education Policy in Egypt"}


def test_thesis_questions_use_filters_and_cite_metadata(thesis_indexes):
    from agentkit.chat import LibraryChat, thesis_filters
    chat = LibraryChat(thesis_indexes["json"], FakeLLM(), cache=None)
    assert thesis_filters("Economics theses since 2020 supervised by Sara Ibrahim", chat._departments()) == {
        "type": "thesis", "year_from": "2020", "advisor": "Sara Ibrahim", "department": "Economics"}
    ans = chat.ask("Theses advised by Laila Nassar")
    assert ans.mode == "answer" and any(s["step"] == "filters" for s in ans.trace), ans.trace
    src = ans.to_dict()["sources"][0]
    assert src["meta"]["type"] == "thesis" and src["meta"]["author"]
    from agentkit.citations import export
    assert "[Master's thesis, The American University in Cairo]" in export([src], "apa")


def test_without_theses_questions_fall_back_to_repository_page(seed_index):
    from agentkit.chat import LibraryChat
    ans = LibraryChat(seed_index, FakeLLM(), cache=None).ask("Where are AUC theses published?")
    assert ans.mode == "answer" and "Knowledge Fountain" in ans.text


def test_search_api_filters(thesis_indexes):
    from fastapi.testclient import TestClient
    from agentkit.api import create_app
    from agentkit.chat import LibraryChat
    client = TestClient(create_app(LibraryChat(thesis_indexes["json"], FakeLLM())))
    r = client.get("/api/search", params={"q": "water", "type": "thesis", "year_to": 2016}).json()
    assert r["results"] and all(int(x["meta"]["year"]) <= 2016 for x in r["results"])
    assert client.get("/api/search", params={"q": "x", "year_from": 1500}).status_code == 422
