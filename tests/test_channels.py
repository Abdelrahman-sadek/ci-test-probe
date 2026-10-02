from agentkit.accessible import export_html
from agentkit.llm import FakeLLM
from agentkit.whatsapp import MAX_TEXT, format_reply, parse_messages


def test_accessible_export_marks_arabic_and_pages(pdfs, tmp_path):
    html = export_html(pdfs[1], FakeLLM(ocr_text="يمكن حجز قاعات المذاكرة الجماعية عبر الإنترنت."), title="الأسئلة الشائعة")
    assert '<html lang="ar" dir="rtl">' in html and 'aria-label="Page 1"' in html and "<title>" in html
    scanned = export_html(pdfs[0], FakeLLM(ocr_text="Group study rooms can be booked online."))
    assert "text recognised by OCR" in scanned and 'lang="en"' in scanned


def test_whatsapp_parse_and_length_cap(chat):
    msgs = parse_messages({"entry": [{"changes": [{"value": {"messages": [
        {"from": "1", "type": "text", "text": {"body": "hi"}}]}}]}]})
    assert msgs == [{"from": "1", "type": "text", "text": "hi"}]
    ans = chat.ask("Can alumni borrow books?")
    ans.text = "x" * 9000
    assert len(format_reply(ans)) == MAX_TEXT
