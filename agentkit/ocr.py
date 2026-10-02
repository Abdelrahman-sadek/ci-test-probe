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


LOW_CONFIDENCE = float(os.getenv("AGENTKIT_LOW_CONFIDENCE", "0.7"))


class ParseError(ValueError):
    """File rejected or failed to parse safely (too large, too many pages, timeout, crash)."""
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass
class Page:
    text: str
    page: int
    method: str  # text | ocr-claude | ocr-tesseract | ocr-none | corrected
    confidence: float = 1.0  # 0–1; pages under LOW_CONFIDENCE go to the staff correction queue


def preprocess(png: bytes) -> bytes:
    """Clean a scan before OCR: grayscale, autocontrast, deskew (projection-profile search over ±5°) and
    upscale small images. Skipped with AGENTKIT_OCR_PREPROCESS=0."""
    if os.getenv("AGENTKIT_OCR_PREPROCESS") == "0":
        return png
    import io
    from PIL import Image, ImageOps
    img = ImageOps.autocontrast(Image.open(io.BytesIO(png)).convert("L"))
    if img.width < 1500:  # OCR quality drops sharply below ~200 DPI
        scale = 1500 / img.width
        img = img.resize((1500, int(img.height * scale)), Image.LANCZOS)
    small = img.resize((max(img.width // 4, 1), max(img.height // 4, 1))).point(lambda v: 255 if v < 128 else 0)

    def score(angle):
        rot = small.rotate(angle, fillcolor=0)
        sums = list(rot.resize((1, rot.height), Image.BOX).tobytes())  # row means, computed in C
        mean = sum(sums) / len(sums)
        return sum((x - mean) ** 2 for x in sums)  # text lines aligned → strong row variance

    best = max((a / 2 for a in range(-10, 11)), key=score)
    if best:
        img = img.rotate(best, expand=True, fillcolor=255)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _tesseract(png: bytes) -> tuple[str, float] | None:
    try:
        import io
        import pytesseract
        from PIL import Image
    except ImportError:
        return None
    if not shutil.which("tesseract"):
        return None
    img = Image.open(io.BytesIO(png))
    data = pytesseract.image_to_data(img, lang="ara+eng", output_type=pytesseract.Output.DICT)
    confs = [float(c) for c in data["conf"] if str(c) not in ("-1", "-1.0")]
    return pytesseract.image_to_string(img, lang="ara+eng"), (sum(confs) / len(confs) / 100 if confs else 0.0)


_MODEL_CONF = {"high": 0.95, "medium": 0.75, "low": 0.45}


def ocr_png(png: bytes, llm: LLM) -> tuple[str, str, float]:
    """Return (text, method, engine confidence)."""
    png = preprocess(png)
    engine = os.getenv("AGENTKIT_OCR", "auto")
    if engine in ("claude", "auto") and (llm.live or engine == "claude" or not shutil.which("tesseract")):
        text = llm.ocr_image(png)
        m = re.search(r"\n?CONFIDENCE:\s*(high|medium|low)\s*$", text, re.I)
        conf = _MODEL_CONF[m.group(1).lower()] if m else 0.9
        return (text[:m.start()] if m else text).strip(), "ocr-claude", conf
    if engine in ("tesseract", "auto"):
        res = _tesseract(png)
        if res is not None:
            return res[0], "ocr-tesseract", res[1]
    return "", "ocr-none", 0.0


def page_confidence(text: str, engine_conf: float = 1.0) -> float:
    """Combine engine confidence with text-quality signals: illegible markers, garbled script, symbol noise."""
    t = text.strip()
    if not t:
        return 0.0
    words_ = t.split()
    illegible = t.count("[illegible]") / max(len(words_), 1)
    noise = sum(1 for ch in t if not (ch.isalnum() or ch.isspace() or ch in ".,;:!?؟،؛()[]-–—'\"/%|#*>«»")) / len(t)
    garble = 0.5 if garbled(t) else 0.0
    return round(max(0.0, min(1.0, engine_conf - 2 * illegible - 3 * noise - garble)), 3)


def _segments(pg, skip) -> list[tuple[float, float, float, float, str]]:
    """Line segments from word boxes. A line is split where the horizontal gap is wide (a column gutter),
    so paragraphs that share a baseline across two columns are not merged."""
    import unicodedata
    lines = {}
    for x0, y0, x1, y1, w, b, ln, _ in pg.get_text("words"):
        if not skip((x0, y0, x1, y1)):
            lines.setdefault((b, ln), []).append((x0, y0, x1, y1, w))
    segs = []
    for words_ in lines.values():
        words_.sort()
        cur = [words_[0]]
        for w in words_[1:]:
            if w[0] - cur[-1][2] > 25:  # gutter
                segs.append(cur)
                cur = []
            cur.append(w)
        segs.append(cur)
    out = []
    for seg in segs:
        toks = [unicodedata.normalize("NFKC", w[4]) for w in seg]
        if sum(bool(re.search(r"[\u0621-\u064A]", t)) for t in toks) > len(toks) / 2:
            toks.reverse()  # boxes are in visual (left→right) order; Arabic reads right→left
        out.append((min(w[0] for w in seg), min(w[1] for w in seg), max(w[2] for w in seg),
                    max(w[3] for w in seg), " ".join(toks)))
    return out


def layout_text(pg) -> str:
    """Reading order for real documents: drop running headers/footers, detect two columns (right-to-left
    pages read the right column first), and render tables as Markdown at their position."""
    height, width = pg.rect.height, pg.rect.width
    tables = []
    try:
        for t in pg.find_tables().tables:
            tables.append((t.bbox, t.to_markdown()))
    except Exception:  # noqa: BLE001 — table detection is best-effort
        tables = []

    def in_table(b):
        return any(b[0] >= tb[0] - 2 and b[1] >= tb[1] - 2 and b[2] <= tb[2] + 2 and b[3] <= tb[3] + 2
                   for tb, _ in tables)

    segs = [s_ for s_ in _segments(pg, in_table) if s_[4].strip()]
    segs = [s_ for s_ in segs if not ((s_[3] < height * 0.06 or s_[1] > height * 0.94) and len(s_[4]) < 80)]
    items = segs + [(tb[0], tb[1], tb[2], tb[3], md.strip()) for tb, md in tables]
    if not items:
        return ""
    mid = width / 2
    left = [i for i in items if i[2] <= mid + 5]
    right = [i for i in items if i[0] >= mid - 5]
    full = [i for i in items if i not in left and i not in right]
    if len(left) >= 2 and len(right) >= 2 and len(full) <= 0.2 * len(items):
        rtl = sum(len(re.findall(r"[\u0621-\u064A]", i[4])) for i in items) > \
            sum(len(re.findall(r"[A-Za-z]", i[4])) for i in items)
        top = min(i[1] for i in left + right)
        cols = [right, left] if rtl else [left, right]
        ordered = ([i for i in sorted(full, key=lambda i: i[1]) if i[1] < top]
                   + [i for col in cols for i in sorted(col, key=lambda i: i[1])]
                   + [i for i in sorted(full, key=lambda i: i[1]) if i[1] >= top])
    else:
        ordered = sorted(items, key=lambda i: (round(i[1] / 5), i[0]))
    out, prev = [], None
    for i in ordered:  # blank line between paragraphs (vertical gap), newline within one
        if prev is not None:
            out.append("\n\n" if i[1] - prev[3] > (prev[3] - prev[1]) * 0.8 or i[1] < prev[1] else "\n")
        out.append(i[4])
        prev = i
    return "".join(out)


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


def ead_to_text(xml: str) -> str:
    """Archival finding aids (EAD 2002/3): collection title, dates, extent, scope note, access/use terms,
    and the series/file list, as Markdown so each part becomes a citable chunk."""
    import xml.etree.ElementTree as ET  # nosec B405 — parsed with entity expansion disabled below
    parser = ET.XMLParser()  # nosec B314 — DTD/entity declarations are rejected below before parsing
    if "<!ENTITY" in xml or "<!DOCTYPE" in xml:  # refuse entity tricks (billion laughs, XXE)
        raise ParseError("finding aid contains a DTD/entities; refusing to parse")
    root = ET.fromstring(xml, parser=parser)  # nosec B314 — DTD/entities rejected above

    def local(el):
        return el.tag.split("}")[-1]

    def find(name):
        return next((e for e in root.iter() if local(e) == name), None)

    def text(el):
        return " ".join(" ".join(el.itertext()).split()) if el is not None else ""

    title = text(find("unittitle")) or text(find("titleproper")) or "Finding aid"
    out = [f"---\ntitle: {title} (finding aid)\n---", f"# {title}", "## Collection summary",
           f"Title: {title}. Dates: {text(find('unitdate')) or 'n.d.'}. Extent: {text(find('extent')) or 'not stated'}. "
           f"Identifier: {text(find('unitid')) or 'n/a'}."]
    for tag, heading in (("scopecontent", "Scope and content"), ("bioghist", "Biographical/historical note"),
                         ("accessrestrict", "Conditions of access"), ("userestrict", "Conditions of use and reproduction")):
        if (el := find(tag)) is not None:
            out += [f"## {heading}", text(el)]
    comps = [e for e in root.iter() if local(e) in ("c", "c01", "c02", "c03")]
    if comps:
        out.append("## Series and files")
        for c in comps:
            did = next((d for d in c if local(d) == "did"), None)
            if did is not None:
                t = text(next((x for x in did if local(x) == "unittitle"), None))
                d = text(next((x for x in did if local(x) == "unitdate"), None))
                b = text(next((x for x in did if local(x) == "container"), None))
                out.append(f"{t}{' (' + d + ')' if d else ''}{', box/folder ' + b if b else ''}.")
    return "\n\n".join(out)


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
        text, method, conf = ocr_png(pymupdf.Pixmap(str(path)).tobytes("png"), llm)
        return [Page(text, 1, method, page_confidence(text, conf))]
    if ext in (".xml", ".ead"):
        return [Page(ead_to_text(path.read_text(encoding="utf-8")), 1, "text")]
    if ext == ".pdf":
        pages = []
        with pymupdf.open(path) as doc:
            if doc.page_count > MAX_PAGES:
                raise ParseError(f"{path.name}: {doc.page_count} pages (max {MAX_PAGES})")
            for i, pg in enumerate(doc, 1):
                text = layout_text(pg)
                if len(text.strip()) >= MIN_TEXT_CHARS and not garbled(text):
                    pages.append(Page(text, i, "text", page_confidence(text)))
                else:
                    ocr_text, method, conf = ocr_png(pg.get_pixmap(dpi=dpi).tobytes("png"), llm)
                    pages.append(Page(ocr_text, i, method, page_confidence(ocr_text, conf)))
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
