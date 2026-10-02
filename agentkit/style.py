"""Answer style filter, adapted from antislop (github.com/miqdadbadjuber/anti-slop, MIT).

Library answers should read like a librarian's note: the fact, the source, the next step. This module holds the
prompt rules, a detector used by evals and tests, and a conservative cleanup that only drops whole filler
sentences at the start or end of an answer, never facts or citations.
"""
import re

RULES = (
    "\n- Write plainly: lead with the answer, then the next step. No greeting, no praise of the question, no"
    " closing offer (\"I hope this helps\", \"feel free to ask\", \"أتمنى أن يكون\", \"لا تتردد\")."
    "\n- No buzzwords (seamless, robust, delve, unlock, empower, cutting-edge) and no claims the sources do not make."
    "\n- Name who acts: \"the circulation desk renews books\", not \"books can be renewed\", when the source says who."
)

# Whole sentences that carry no information. Matched case-insensitively at a sentence start.
FILLER_OPENERS = [
    r"great question", r"good question", r"certainly", r"of course", r"absolutely", r"sure thing",
    r"i'?d be (happy|glad) to help", r"happy to help", r"thanks for (asking|your question)",
    r"let'?s dive in", r"here'?s what you need to know", r"as an ai( language model)?",
    r"سؤال (رائع|ممتاز|جميل)", r"بكل سرور", r"يسعدني (مساعدتك|أن أساعدك)", r"بالتأكيد",
]
FILLER_CLOSERS = [
    r"i hope (this|that) helps", r"hope (this|that) helps", r"let me know if", r"feel free to",
    r"don'?t hesitate to", r"is there anything else", r"would you like me to", r"happy (learning|reading)",
    r"أتمنى أن (يكون|تكون)", r"لا تتردد", r"هل (هناك|في) أي (شيء|حاجة) (آخر|تانية)", r"بالتوفيق",
]
BUZZWORDS = [
    r"delve", r"seamless(ly)?", r"robust", r"elevate", r"unlock", r"empower", r"cutting[- ]edge",
    r"game[- ]changer", r"next[- ]level", r"testament to", r"vibrant", r"treasure trove", r"rich tapestry",
    r"embark on", r"navigate the (world|landscape)", r"in today'?s (digital|fast-paced)",
]
_OPEN = re.compile(r"^\s*(" + "|".join(FILLER_OPENERS) + r")\b[^.!?؟\n]*[.!?؟]*\s*", re.I)
_CLOSE = re.compile(r"(?:^|(?<=[.!?؟\n]))\s*(" + "|".join(FILLER_CLOSERS) + r")\b[^\n]*?[.!?؟]?\s*$", re.I)
_BUZZ = re.compile(r"\b(" + "|".join(BUZZWORDS) + r")\b", re.I)
_CAPS = re.compile(r"\b[A-Z]{4,}(?:\s+[A-Z]{4,}){2,}\b")  # three or more shouted words in a row


def findings(text: str) -> list[str]:
    """Slop patterns present in an answer, e.g. ["opener: great question", "buzzword: seamless"]."""
    out = []
    if m := _OPEN.match(text):
        out.append("opener: " + m.group(1).lower())
    body = text.split("\n\nSources:")[0]
    if m := _CLOSE.search(body):
        out.append("closer: " + m.group(1).lower())
    out += ["buzzword: " + m.group(1).lower() for m in _BUZZ.finditer(body)]
    out += ["shouting: " + m.group(0) for m in _CAPS.finditer(body)]
    return out


def clean(text: str) -> str:
    """Drop a filler opening or closing sentence. Citations, facts and the Sources block are left alone."""
    body, sep, sources = text.partition("\n\nSources:")
    def drop(m):  # keep any sentence that carries a citation marker
        return m.group(0) if re.search(r"\[\d+\]", m.group(0)) else ""
    trimmed = _OPEN.sub(drop, body, count=1)
    for _ in range(2):  # a closer can be two short sentences ("Hope this helps! Let me know if…")
        trimmed = _CLOSE.sub(drop, trimmed).rstrip()
    if not trimmed.strip():
        return text  # never return an empty answer
    return trimmed + sep + sources
