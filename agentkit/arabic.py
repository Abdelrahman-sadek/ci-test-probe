"""Arabic/English text normalisation, tokenisation and language detection."""
import re
import unicodedata

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")  # harakat + tatweel
_ALEF = re.compile(r"[إأآٱ]")
_TOKEN = re.compile(r"[\w؀-ۿ]+", re.UNICODE)
_ARABIC_CHAR = re.compile(r"[؀-ۿ]")
# Arabizi: Latin words containing the digits used for Arabic letters (2,3,5,6,7,8,9)
_ARABIZI = re.compile(r"\b[a-z]*[235679][a-z]+[a-z0-9]*\b", re.I)

AR_STOP = {"في", "من", "على", "الى", "عن", "ما", "هل", "او", "و", "ان", "هو", "هي", "كام", "ايه", "اللي", "النهارده"}
EN_STOP = {"the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "and", "or", "do", "does", "i", "can",
           "how", "what", "my", "me", "it", "at", "be", "with", "this", "that", "you", "your"}


def normalize(text: str) -> str:
    """NFKC (fixes PDF presentation forms like 'ﻣﻮﺍﻋﻴﺪ'), strip diacritics, unify alef/yaa/taa marbuta."""
    text = unicodedata.normalize("NFKC", text)
    text = _DIACRITICS.sub("", text)
    text = _ALEF.sub("ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return text.lower()


def _stem_ar(tok: str) -> str:
    # Light prefix stripping: definite article and attached conjunctions/prepositions.
    for p in ("وال", "بال", "كال", "فال", "لل", "ال"):
        if tok.startswith(p) and len(tok) - len(p) >= 2:
            return tok[len(p):]
    return tok


def tokenize(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(normalize(text)):
        if _ARABIC_CHAR.search(tok):
            if tok in AR_STOP:
                continue
            tok = _stem_ar(tok)
            if tok not in AR_STOP and len(tok) > 1:
                out.append(tok)
        elif tok not in EN_STOP and len(tok) > 1:
            out.append(tok[:-1] if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss") else tok)
    return out


def detect_lang(text: str) -> str:
    """Return 'ar', 'arabizi', or 'en'."""
    letters = [c for c in text if c.isalpha()]
    if letters and sum(bool(_ARABIC_CHAR.match(c)) for c in letters) / len(letters) > 0.3:
        return "ar"
    if len(_ARABIZI.findall(text)) >= 2:
        return "arabizi"
    return "en"
