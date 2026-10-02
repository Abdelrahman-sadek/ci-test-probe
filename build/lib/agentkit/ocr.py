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
MAX_FILE_MB = float(os.getenv("AGENTKIT_MAX_FILE_MB", "25"))
MAX_PAGES = int(os.getenv("AGENTKIT_MAX_PAGES", "500"))
PARSE_TIMEOUT = float(os.getenv("AGENTKIT_PARSE_TIMEOUT", "120"))
PARSE_MEMORY_MB = int(os.getenv("AGENTKIT_PARSE_MEMORY_MB", "2048"))


class ParseError(ValueError):
    """File rejected or failed to parse safely (too large, too many pages, timeout, crash)."""
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


_PRESENTATION = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")


def fix_rtl(text: str) -> str:
    """PDF text layers often store Arabic as presentation-form glyphs (ﻣﻮﺍﻋﻴﺪ) in visual order. Convert those
    lines to normal letters (NFKC) and restore logical word order, so search, quotes and screen readers work."""
    import unicodedata
    out = []
    for line in text.splitlines():
        if _PRESENTATION.search(line):
            line = " ".join(reversed(unicodedata.normalize("NFKC", line).split()))
        out.append(line)
    return "\n".join(out)


_MIXED = re.compile(r"[\u0621-\u064A][A-Za-z\u0180-\u024F]|[A-Za-z\u0180-\u024F][\u0621-\u064A]")


def garbled(text: str) -> bool:
    """A broken ToUnicode map shows up as Latin letters glued inside Arabic words (e.g. 'اDŽمذاكرة').
    Such text layers are unusable, so the page is OCR'd instead."""
    arabic_words = re.findall(r"\S*[\u0621-\u064A]\S*", text)
    return bool(arabic_words) and sum(bool(_MIXED.search(w)) for w in arabic_words) / len(arabic_words) > 0.05


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|footer|header).*?</\1>", " ", html)
    html = re.sub(r"(?i)<(h[1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)[1]) + " ", html)
    html = re.sub(r"(?i)<(br|/p|/li|/tr|/h[1-6]|/div)[^>]*>", "\n", html)
    return unescape(re.sub(r"<[^>]+>", " ", html))


def extract(path: str | Path, llm: LLM, dpi: int = 200) -> list[Page]:
    path = Path(path)
    ext = path.suffix.lower()
    if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
        raise ParseError(f"{path.name}: larger than {MAX_FILE_MB:g} MB")
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
            if doc.page_count > MAX_PAGES:
                raise ParseError(f"{path.name}: {doc.page_count} pages (max {MAX_PAGES})")
            for i, pg in enumerate(doc, 1):
                text = pg.get_text("text", sort=True)
                text = fix_rtl(text)
                if len(text.strip()) >= MIN_TEXT_CHARS and not garbled(text):
                    pages.append(Page(text, i, "text"))
                else:
                    ocr_text, method = ocr_png(pg.get_pixmap(dpi=dpi).tobytes("png"), llm)
                    pages.append(Page(ocr_text, i, method))
        return pages
    raise ValueError(f"Unsupported file type: {path}")


def _child(extractor, path, llm, conn):
    try:
        import resource
        limit = PARSE_MEMORY_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))  # a malicious PDF can't exhaust memory
    except (ImportError, ValueError, OSError):
        pass
    try:
        conn.send(("ok", extractor(path, llm)))
    except Exception as e:  # noqa: BLE001 — report any parser failure to the parent
        conn.send(("error", f"{type(e).__name__}: {e}"))
    finally:
        conn.close()


def safe_extract(path: str | Path, llm: LLM, timeout: float | None = None, extractor=extract) -> list[Page]:
    """Parse in a forked child process with a memory cap and a timeout, so a hostile or broken file can't
    hang or crash the server. Falls back to in-process parsing where fork isn't available."""
    import multiprocessing as mp
    if "fork" not in mp.get_all_start_methods() or os.getenv("AGENTKIT_SAFE_PARSE") == "0":
        return extractor(path, llm)
    ctx = mp.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(extractor, path, llm, child), daemon=True)
    proc.start()
    child.close()
    timeout = PARSE_TIMEOUT if timeout is None else timeout
    if not parent.poll(timeout):
        proc.kill()
        proc.join()
        raise ParseError(f"{Path(path).name}: parsing timed out after {timeout:g}s")
    status, payload = parent.recv()
    proc.join()
    if status != "ok":
        raise ParseError(f"{Path(path).name}: {payload}")
    return payload
