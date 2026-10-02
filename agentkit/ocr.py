"""Extract text from PDFs, scans, images, HTML, and text files. Pages without a text layer are OCR'd.

OCR engines (AGENTKIT_OCR): "auto" (default: Claude vision if live, else Tesseract if installed),
"claude", "tesseract", "none".
"""
import os
import re
import shutil
from dataclasses import dataclass
from html import unescape
from pathlib import Path

import pymupdf

from .llm import LLM

MIN_TEXT_CHARS = 40  # below this a PDF page is treated as scanned
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass
class Page:
    text: str
    page: int
    method: str  # text | ocr-claude | ocr-tesseract | ocr-none


def _tesseract(png: bytes) -> str | None:
    try:
        import io
        import pytesseract
        from PIL import Image
    except ImportError:
        return None
    if not shutil.which("tesseract"):
        return None
    return pytesseract.image_to_string(Image.open(io.BytesIO(png)), lang="ara+eng")


def ocr_png(png: bytes, llm: LLM) -> tuple[str, str]:
    engine = os.getenv("AGENTKIT_OCR", "auto")
    if engine in ("claude", "auto") and (llm.live or engine == "claude" or not shutil.which("tesseract")):
        return llm.ocr_image(png), "ocr-claude"
    if engine in ("tesseract", "auto"):
        text = _tesseract(png)
        if text is not None:
            return text, "ocr-tesseract"
    return "", "ocr-none"


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|footer|header).*?</\1>", " ", html)
    html = re.sub(r"(?i)<(h[1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)[1]) + " ", html)
    html = re.sub(r"(?i)<(br|/p|/li|/tr|/h[1-6]|/div)[^>]*>", "\n", html)
    return unescape(re.sub(r"<[^>]+>", " ", html))


def extract(path: str | Path, llm: LLM, dpi: int = 200) -> list[Page]:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        return [Page(path.read_text(encoding="utf-8"), 1, "text")]
    if ext in (".html", ".htm"):
        return [Page(_html_to_text(path.read_text(encoding="utf-8")), 1, "text")]
    if ext in IMAGE_EXT:
        text, method = ocr_png(pymupdf.Pixmap(str(path)).tobytes("png"), llm)
        return [Page(text, 1, method)]
    if ext == ".pdf":
        pages = []
        with pymupdf.open(path) as doc:
            for i, pg in enumerate(doc, 1):
                text = pg.get_text("text", sort=True)
                if len(text.strip()) >= MIN_TEXT_CHARS:
                    pages.append(Page(text, i, "text"))
                else:
                    ocr_text, method = ocr_png(pg.get_pixmap(dpi=dpi).tobytes("png"), llm)
                    pages.append(Page(ocr_text, i, method))
        return pages
    raise ValueError(f"Unsupported file type: {path}")
