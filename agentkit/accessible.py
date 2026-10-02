"""Export any ingested document (including scanned PDFs, via OCR) as accessible HTML: a real <title>,
language and direction per paragraph, headings, page landmarks — readable by screen readers and
reflowable for low vision (WCAG 2.2: 1.3.1 Info and Relationships, 3.1.2 Language of Parts, 1.4.10 Reflow)."""
import html
import re
from pathlib import Path

from .arabic import detect_lang
from .ocr import safe_extract


def _lang_attrs(text: str) -> str:
    return ' lang="ar" dir="rtl"' if detect_lang(text) == "ar" else ' lang="en" dir="ltr"'


def export_html(path: str | Path, llm, title: str = "") -> str:
    path = Path(path)
    pages = safe_extract(path, llm)
    title = title or path.stem.replace("-", " ").title()
    doc_lang = "ar" if detect_lang(" ".join(p.text for p in pages)[:2000]) == "ar" else "en"
    out = [f'<!doctype html><html lang="{doc_lang}" dir="{"rtl" if doc_lang == "ar" else "ltr"}"><head>',
           '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           f"<title>{html.escape(title)}</title>",
           "<style>body{max-width:70ch;margin:auto;padding:1rem;font:1.1rem/1.6 system-ui,Tahoma,sans-serif}"
           "section{border-top:1px solid #888;margin-top:1.5rem}</style></head><body>",
           f"<main><h1>{html.escape(title)}</h1>"]
    for p in pages:
        out.append(f'<section aria-label="Page {p.page}"><p class="page-label">Page {p.page}'
                   f'{" (text recognised by OCR)" if p.method.startswith("ocr") else ""}</p>')
        text = re.sub(r"^---\n.*?\n---\n", "", p.text, flags=re.S)
        for block in re.split(r"\n\s*\n", text):
            block = block.strip()
            if not block:
                continue
            m = re.match(r"^(#{1,5})\s+(.*)$", block)
            if m:
                lvl = min(len(m.group(1)) + 1, 6)
                out.append(f"<h{lvl}{_lang_attrs(m.group(2))}>{html.escape(m.group(2))}</h{lvl}>")
            else:
                body = html.escape(" ".join(block.split()))
                out.append(f"<p{_lang_attrs(block)}>{body}</p>")
        out.append("</section>")
    out.append("</main></body></html>")
    return "\n".join(out)
