"""Plan 7 phase C: reference export in six formats, from library pages, theses and catalog records."""
import json

from agentkit.citations import export, item

PAGE = {"title": "Borrow and Renew Books", "section": "Loan periods and limits",
        "url": "https://library.aucegypt.edu/services/borrow-renew-books", "updated": "2026-10-02"}
THESIS = {"title": "Water Policy in the Nile Delta", "url": "https://fount.aucegypt.edu/etds/1234", "updated": "",
          "meta": {"type": "thesis", "author": "Hassan, Mona Ali", "year": "2019", "degree": "Master of Arts",
                   "advisor": "Ibrahim, Sara", "department": "Public Policy", "repository": "AUC Knowledge Fountain"}}
BOOK = {"title": "Cairo: The City Victorious", "url": "", "meta": {"type": "book", "author": "Max Rodenbeck",
                                                                    "year": "1999", "publisher": "Knopf", "isbn": "0679444513"}}
DAY = "2026-10-02"


def test_apa():
    out = export([PAGE, THESIS, BOOK], "apa", accessed=DAY).split("\n\n")
    assert out[0].startswith("The American University in Cairo Libraries. (2026, October 2). Borrow and Renew Books: "
                             "Loan periods and limits. https://library.aucegypt.edu/")
    assert out[1] == ("Hassan, M. A. (2019). Water Policy in the Nile Delta [Master's thesis, The American University "
                      "in Cairo]. AUC Knowledge Fountain. https://fount.aucegypt.edu/etds/1234")
    assert out[2].strip() == "Rodenbeck, M. (1999). Cairo: The City Victorious. Knopf."


def test_mla():
    out = export([PAGE, THESIS, BOOK], "mla", accessed=DAY).split("\n\n")
    assert out[0] == ("“Loan periods and limits.” Borrow and Renew Books, The American University in Cairo "
                      "Libraries, 2 Oct. 2026, library.aucegypt.edu/services/borrow-renew-books. Accessed 2 Oct. 2026.")
    assert out[1].startswith("Hassan, Mona Ali. Water Policy in the Nile Delta. 2019. The American University in Cairo, "
                             "Master's thesis.")
    assert out[2].strip() == "Rodenbeck, Max. Cairo: The City Victorious. Knopf, 1999."


def test_bibtex_ris_enw_csl():
    bib = export([PAGE, THESIS, BOOK], "bibtex", accessed=DAY)
    assert "@online{auclib2026borrow," in bib and "@mastersthesis{hassan2019water," in bib and "@book{rodenbeck1999cairo," in bib
    assert "note = {Advisor: Ibrahim, Sara}" in bib and "isbn = {0679444513}" in bib
    ris = export([THESIS], "ris", accessed=DAY)
    assert ris.splitlines()[:3] == ["TY  - THES", "AU  - Hassan, Mona Ali", "TI  - Water Policy in the Nile Delta"]
    assert ris.rstrip().endswith("ER  -")
    assert "%0 Web Page" in export([PAGE], "enw", accessed=DAY)
    data = json.loads(export([THESIS, BOOK], "csl", accessed=DAY))
    assert data[0]["type"] == "thesis" and data[0]["author"][0] == {"family": "Hassan", "given": "Mona Ali"}
    assert data[1]["ISBN"] == "0679444513" and data[1]["issued"] == {"date-parts": [[1999]]}


def test_nothing_is_invented():
    it = item({"title": "Untitled page", "url": "", "updated": ""})
    assert it["authors"] == [] and it["year"] == "" and it["url"] == ""
    assert "n.d." in export([{"title": "Untitled page"}], "apa")
    for fmt in ("bibtex", "ris", "enw"):
        out = export([{"title": "Untitled page"}], fmt)
        assert "isbn" not in out.lower() and "doi" not in out.lower()


def test_arabic_titles_kept_and_bibtex_escaped():
    out = export([{"title": "الاستعارة والتجديد {draft} & 100%", "url": "https://library.aucegypt.edu/x"}], "bibtex")
    assert "الاستعارة والتجديد \\{draft\\} \\& 100\\%" in out


def test_cite_endpoint(seed_index):
    from fastapi.testclient import TestClient
    from agentkit.api import create_app
    from agentkit.chat import LibraryChat
    from agentkit.llm import FakeLLM
    client = TestClient(create_app(LibraryChat(seed_index, FakeLLM())))
    ans = client.post("/api/ask", json={"question": "How many books can alumni borrow?"}).json()
    r = client.post("/api/cite", json={"format": "ris", "sources": ans["sources"]})
    assert r.status_code == 200 and "TY  - ELEC" in r.text and "attachment" in r.headers["content-disposition"]
    assert client.post("/api/cite", json={"format": "docx", "sources": ans["sources"]}).status_code == 422
    evil = {"format": "apa", "sources": [{"title": "x", "meta": {"script": "<b>", "author": "A" * 5000}}]}
    assert len(client.post("/api/cite", json=evil).text) < 700  # unknown fields dropped, long ones cut
