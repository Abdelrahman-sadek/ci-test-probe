from agentkit.arabic import normalize
from agentkit.llm import FakeLLM
from agentkit.ocr import extract
from agentkit.rag import Index, ingest


def test_scanned_pdf_goes_to_ocr(pdfs):
    pages = extract(pdfs[0], FakeLLM(ocr_text="Group study rooms can be booked"))
    assert pages[0].method.startswith("ocr") and "study rooms" in pages[0].text


AR_OCR = "يمكن حجز قاعات المذاكرة الجماعية عبر الإنترنت لمدة ساعتين يوميًا."


def test_broken_arabic_text_layer_goes_to_ocr_and_is_searchable(pdfs):
    pages = extract(pdfs[1], FakeLLM(ocr_text=AR_OCR))  # fixture font has a broken lam mapping
    assert pages[0].method.startswith("ocr") and normalize("قاعات") in normalize(pages[0].text)
    assert Index(ingest([pdfs[1]], FakeLLM(ocr_text=AR_OCR))[0].chunks).search("حجز قاعات المذاكرة")


def test_presentation_forms_fixed_and_garble_detected():
    from agentkit.ocr import fix_rtl, garbled
    assert fix_rtl("ﺍﻟﻄﺒﺎﻋﺔ ﻣﺘﺎﺣﺔ") == "متاحة الطباعة"  # NFKC letters, logical word order
    assert garbled("يمكن حجز قاعات اDŽمذاكرة اDŽجماعية") and not garbled("يمكن حجز قاعات المذاكرة")
