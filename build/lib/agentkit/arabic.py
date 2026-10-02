"""Arabic/English text normalisation, light stemming, sentence splitting, language and Franco-Arabic handling."""
import re
import unicodedata

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")  # harakat + tatweel
_ALEF = re.compile(r"[إأآٱ]")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")  # Arabic-Indic + Persian
_TOKEN = re.compile(r"\w+")  # \w covers Arabic letters and digits, not punctuation such as ؟ ، ؛
_ARABIC_CHAR = re.compile(r"[؀-ۿ]")
# Arabizi: Latin words containing the digits used for Arabic letters (2,3,5,6,7,8,9)
_ARABIZI = re.compile(r"\b[a-z]*[235679][a-z]+[a-z0-9]*\b", re.I)
_TANWEEN_ALEF = re.compile(r"\u064Bا|ا\u064B")
_SENTENCE = re.compile(r"(?<=[.!?؟؛۔])\s+|\n+")

AR_STOP = {"ال", "في", "من", "على", "الى", "عن", "ما", "هل", "او", "و", "ان", "هو", "هي", "كام", "ايه", "اللي", "النهارده",
           "ممكن", "عايز", "عاوز", "انا", "مع", "كل", "هذا", "هذه", "ذلك", "التي", "الذي", "لا", "يا", "بس"}
EN_STOP = {"the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "and", "or", "do", "does", "i", "can",
           "how", "what", "my", "me", "it", "at", "be", "with", "this", "that", "you", "your", "there", "any",
           "please", "about", "from", "by", "as", "if", "where", "when", "which", "who", "will", "would", "s"}

# Light10 (Larkey et al., UMass): strip "و", then articles, then suffixes — no root extraction.
_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")
_AR_SUFFIXES = ("ها", "ان", "ات", "ون", "ين", "يه", "ه", "ي")  # ة/ية already normalised to ه/يه


def normalize(text: str) -> str:
    """NFKC (fixes PDF presentation forms like 'ﻣﻮﺍﻋﻴﺪ'), unify digits/letters, strip diacritics and tatweel."""
    text = unicodedata.normalize("NFKC", text).translate(_DIGITS)
    text = _TANWEEN_ALEF.sub("", text)  # كتابًا → كتاب
    text = _DIACRITICS.sub("", text)
    text = _ALEF.sub("ا", text)
    for src, dst in (("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي"), ("ی", "ي"), ("ک", "ك")):
        text = text.replace(src, dst)
    return text.lower()


def stem_ar(tok: str) -> str:
    if tok.startswith("و") and len(tok) > 3:
        tok = tok[1:]
    for p in _AR_PREFIXES:
        if tok.startswith(p) and len(tok) - len(p) >= 2:
            tok = tok[len(p):]
            break
    for s in _AR_SUFFIXES:
        if tok.endswith(s) and len(tok) - len(s) >= 2:
            tok = tok[: -len(s)]
    return tok


def _stem_en(tok: str) -> str:
    """Tiny suffix stripper: enough to match borrow/borrowed, close/closing, library/libraries."""
    if len(tok) > 4 and tok.endswith("ies"):
        tok = tok[:-3] + "y"
    elif len(tok) > 5 and tok.endswith("ing"):
        tok = tok[:-3]
    elif len(tok) > 4 and tok.endswith("ed"):
        tok = tok[:-2]
    elif len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        tok = tok[:-1]
    return tok[:-1] if len(tok) > 4 and tok.endswith("e") else tok


def words(text: str) -> list[str]:
    """Normalised words without stopwords (unstemmed) — input for character n-grams."""
    out = []
    for tok in _TOKEN.findall(normalize(text)):
        stop = AR_STOP if _ARABIC_CHAR.search(tok) else EN_STOP
        if tok not in stop and (len(tok) > 1 or tok.isdigit()):
            out.append(tok)
    return out


def tokenize(text: str) -> list[str]:
    """Normalised, stopword-free, light-stemmed tokens — input for BM25."""
    out = []
    for tok in words(text):
        tok = stem_ar(tok) if _ARABIC_CHAR.search(tok) else _stem_en(tok)
        if tok not in AR_STOP and (len(tok) > 1 or tok.isdigit()):
            out.append(tok)
    return out


def sentences(text: str) -> list[str]:
    """Sentence-aware split for Arabic and English (best chunking strategy for Arabic RAG, arXiv 2506.06339)."""
    return [s.strip() for s in _SENTENCE.split(text) if s and s.strip()]


def detect_lang(text: str) -> str:
    """Return 'ar', 'arabizi', or 'en'."""
    letters = [c for c in text if c.isalpha()]
    if letters and sum(bool(_ARABIC_CHAR.match(c)) for c in letters) / len(letters) > 0.3:
        return "ar"
    if len(_ARABIZI.findall(text)) >= 2:
        return "arabizi"
    return "en"


# Franco-Arabic → Arabic candidates. There is no standard spelling, so we emit two variants
# (short "a" dropped / written as alef); character n-gram retrieval tolerates the remaining noise.
_FRANCO_MULTI = (("3'", "غ"), ("7'", "خ"), ("sh", "ش"), ("ch", "ش"), ("kh", "خ"), ("gh", "غ"), ("th", "ث"),
                 ("dh", "ذ"), ("ou", "و"), ("oo", "و"), ("ee", "ي"), ("aa", "ا"))
_FRANCO_ONE = {"2": "ء", "3": "ع", "5": "خ", "6": "ط", "7": "ح", "8": "غ", "9": "ق", "b": "ب", "t": "ت", "g": "ج",
               "j": "ج", "d": "د", "r": "ر", "z": "ز", "s": "س", "f": "ف", "q": "ق", "k": "ك", "l": "ل", "m": "م",
               "n": "ن", "h": "ه", "w": "و", "y": "ي", "i": "ي", "o": "و", "u": "و", "p": "ب", "v": "ف", "c": "ك",
               "x": "كس", "e": ""}


def _franco_word(word: str, full: bool) -> str:
    """full=False drops short vowels (a/e/o/u) inside words; full=True writes them as ا/و."""
    out, i = [], 0
    while i < len(word):
        two = word[i:i + 2]
        multi = next((ar for src, ar in _FRANCO_MULTI if two == src), None)
        if multi is not None:
            out.append(multi)
            i += 2
            continue
        ch = word[i]
        if ch in "aeiou" and i == 0:
            out.append("ا")
        elif ch == "a" and i == len(word) - 1:
            out.append("ه")  # final -a is usually taa marbuta (maktaba → مكتبة)
        elif ch in "aou" and not full:
            pass
        elif ch == "a":
            out.append("ا")
        else:
            out.append(_FRANCO_ONE.get(ch, ""))
        i += 1
    return "".join(out)


def franco_to_arabic(text: str) -> list[str]:
    """Return up to two Arabic-script candidates for a Franco-Arabic phrase."""
    words = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if not w.isdigit()]
    variants = set()
    for full in (False, True):
        out, article = [], ""
        for w in words:
            if w in ("el", "al", "il"):
                article = "ال"  # "el maktaba" → "المكتبه"
                continue
            out.append(article + _franco_word(w, full))
            article = ""
        variants.add(" ".join(x for x in out if x))
    return sorted(v for v in variants if v.strip())
