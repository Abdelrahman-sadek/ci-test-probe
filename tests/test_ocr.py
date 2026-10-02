from agentkit.arabic import normalize
from agentkit.llm import FakeLLM
from agentkit.ocr import extract
from agentkit.rag import Index, ingest


def test_scanned_pdf_goes_to_ocr(pdfs):
    pages = extract(pdfs[0], FakeLLM(ocr_text="Group study rooms can be booked"))
    assert pages[0].method.startswith("ocr") and "study rooms" in pages[0].text


def test_arabic_text_layer_is_searchable(pdfs):
    pages = extract(pdfs[1], FakeLLM())
    assert pages[0].method == "text" and normalize("قاعات") in normalize(pages[0].text)
    assert Index(ingest([pdfs[1]], FakeLLM())[0].chunks).search("حجز قاعات المذاكرة")
