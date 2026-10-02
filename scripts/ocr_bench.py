"""OCR accuracy benchmark: character/word error rate (CER/WER) against ground truth.

Synthetic set (default): known English and Arabic text rendered to images under conditions that matter for
library scans — clean, rotated 2°, blurred, noisy, low resolution. Real set: --gt-dir with page images
(png/jpg) and a matching .txt ground truth each (e.g. 30 hand-checked AUC scans).

    python scripts/ocr_bench.py --engine tesseract --target-cer 0.10
    python scripts/ocr_bench.py --gt-dir benchmarks/auc-scans --engine claude
"""
import argparse
import io
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentkit.arabic import normalize  # noqa: E402

SAMPLES = {
    "en": "Undergraduate students can borrow up to twenty books for twenty-eight days. Books can be renewed "
          "online, in person or by email before the due date.",
    "ar": "يمكن لطلاب البكالوريوس استعارة الكتب لمدة ثمانية وعشرين يوما. ويمكن تجديد الكتب عبر الإنترنت "
          "أو شخصيا قبل موعد الاستحقاق.",
    "ar-diacritized": "يُمْكِنُ لِلطُّلَّابِ اسْتِعَارَةُ الْكُتُبِ مِنَ الْمَكْتَبَةِ الرَّئِيسِيَّةِ وَتَجْدِيدُهَا قَبْلَ مَوْعِدِ الِاسْتِحْقَاقِ.",
}


def levenshtein(a, b) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def rates(truth: str, got: str) -> tuple[float, float]:
    t, g = " ".join(normalize(truth).split()), " ".join(normalize(got).split())
    cer = levenshtein(t, g) / max(len(t), 1)
    wer = levenshtein(t.split(), g.split()) / max(len(t.split()), 1)
    return cer, wer


def render(text: str, lang: str) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=300)
    d = ' dir="rtl"' if lang == "ar" else ""
    pg.insert_htmlbox(pymupdf.Rect(30, 30, 565, 280), f'<p{d} style="font-size:15px">{text}</p>')
    return pg.get_pixmap(dpi=200).tobytes("png")


def _png(img) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def degrade(png: bytes, condition: str) -> bytes:
    from PIL import Image, ImageFilter
    img = Image.open(io.BytesIO(png)).convert("L")
    if condition == "rotated":
        img = img.rotate(2, expand=True, fillcolor=255)
    elif condition == "blurred":
        img = img.filter(ImageFilter.GaussianBlur(1.2))
    elif condition == "noisy":
        rnd = random.Random(1)
        px = img.load()
        for _ in range(img.width * img.height // 40):
            px[rnd.randrange(img.width), rnd.randrange(img.height)] = rnd.choice((0, 255))
    elif condition == "low-res":
        img = img.resize((img.width // 3, img.height // 3)).resize((img.width, img.height))
    elif condition == "combined":  # photocopy of a photocopy: tilted, soft, speckled, low resolution
        for c in ("rotated", "low-res", "noisy"):
            img = Image.open(io.BytesIO(degrade(_png(img), c))).convert("L")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="tesseract", choices=["tesseract", "claude"])
    ap.add_argument("--gt-dir")
    ap.add_argument("--target-cer", type=float, default=0)
    a = ap.parse_args()
    os.environ["AGENTKIT_OCR"] = a.engine
    from agentkit.llm import get_llm
    from agentkit.ocr import ocr_png
    llm = get_llm()
    cases = []
    if a.gt_dir:
        for img in sorted(Path(a.gt_dir).glob("*")):
            if img.suffix.lower() in (".png", ".jpg", ".jpeg") and img.with_suffix(".txt").exists():
                cases.append((img.stem, img.with_suffix(".txt").read_text(encoding="utf-8"), img.read_bytes()))
    else:
        for lang, text in SAMPLES.items():
            base = render(text, "ar" if lang.startswith("ar") else lang)
            for cond in ("clean", "rotated", "blurred", "noisy", "low-res", "combined"):
                cases.append((f"{lang}-{cond}", text, base if cond == "clean" else degrade(base, cond)))
    total_c = total_w = 0.0
    print(f"{'case':18} {'CER':>6} {'WER':>6}  engine={a.engine}")
    for name, truth, png in cases:
        got, method, conf = ocr_png(png, llm)
        cer, wer = rates(truth, got)
        total_c += cer
        total_w += wer
        print(f"{name:18} {cer:6.3f} {wer:6.3f}  conf={conf:.2f}")
    mean_c, mean_w = total_c / len(cases), total_w / len(cases)
    print(f"{'MEAN':18} {mean_c:6.3f} {mean_w:6.3f}")
    sys.exit(1 if a.target_cer and mean_c > a.target_cer else 0)


if __name__ == "__main__":
    main()
