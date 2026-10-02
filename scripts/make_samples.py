"""Generate PDF test fixtures: an image-only 'scanned' PDF (needs OCR) and an Arabic text-layer PDF.

    python scripts/make_samples.py [outdir]   (default: samples/auc-library)
"""
import sys
from pathlib import Path

import pymupdf

SCANNED_TEXT = """Study Rooms (SAMPLE)
Group study rooms on the second floor can be booked online for up to 2 hours per day.
Rooms hold 4 to 8 students. Printing and scanning are available on the ground floor; printing costs 1 EGP per page."""

ARABIC_HTML = """<h2 dir="rtl">الأسئلة الشائعة (عينة)</h2>
<p dir="rtl">يمكن حجز قاعات المذاكرة الجماعية عبر الإنترنت لمدة ساعتين يوميًا.</p>
<p dir="rtl">الطباعة متاحة في الدور الأرضي بسعر جنيه واحد للصفحة.</p>"""


def make(outdir: Path) -> tuple[Path, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    # 1) Draw text, rasterise it, put only the image into a new PDF → no text layer, like a real scan.
    src = pymupdf.open()
    src.new_page().insert_textbox(pymupdf.Rect(60, 60, 540, 400), SCANNED_TEXT, fontsize=13)
    pix = src[0].get_pixmap(dpi=150)
    scanned = pymupdf.open()
    scanned.new_page().insert_image(scanned[0].rect, pixmap=pix)
    scanned_path = outdir / "scanned-study-rooms.pdf"
    scanned.save(scanned_path)
    # 2) Arabic text layer — extracts as presentation forms (ﻣﻮﺍﻋﻴﺪ), exercising NFKC normalisation.
    ar = pymupdf.open()
    ar.new_page().insert_htmlbox(pymupdf.Rect(50, 50, 550, 400), ARABIC_HTML)
    ar_path = outdir / "arabic-faq.pdf"
    ar.save(ar_path)
    return scanned_path, ar_path


if __name__ == "__main__":
    for p in make(Path(sys.argv[1] if len(sys.argv) > 1 else "samples/auc-library")):
        print("wrote", p)
