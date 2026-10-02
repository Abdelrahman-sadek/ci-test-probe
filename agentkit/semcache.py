"""Semantic answer cache: reuse an answer for a paraphrase of a question asked before, only when it is safe.

A hit needs cosine similarity ≥ AGENTKIT_SEMCACHE_THRESHOLD (0.88) between question vectors AND an identical
signature: index version, access level, language, routed agent, audience terms (alumni ≠ undergraduates), numbers
and question type (where ≠ when). Answers built on live data (hours, rooms, catalog availability) or notices are never stored.

Vectors: character-trigram counts by default (no model, well under a millisecond); set AGENTKIT_SEMCACHE_MODEL to
a small sentence-transformers model (e.g. intfloat/multilingual-e5-small) for paraphrases that share few words.
Storage: in-process; with AGENTKIT_REDIS_URL, a Redis hash per signature so several workers share entries.
"""
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import asdict

from .arabic import normalize, tokenize

LIVE_METHODS = {"live-hours", "live-rooms", "live-catalog", "notice"}
_AUD = re.compile(r"\b(alumn\w*|undergrad\w*|graduate|postgrad\w*|faculty|staff|visitor\w*|external|researcher\w*|"
                  r"emerit\w*|adjunct|student\w*|خريج\w*|طلاب|طالب|بكالوريوس|دراسات عليا|اعضاء|زوار|باحث\w*)\b")
_NUM = re.compile(r"\d+")
QWORDS = {"where": "where", "fen": "where", "feen": "where", "فين": "where", "اين": "where",
          "when": "when", "emta": "when", "امتى": "when", "متى": "when", "time": "when", "hours": "when",
          "who": "who", "meen": "who", "مين": "who", "من": "who",
          "why": "why", "leh": "why", "ليه": "why", "لماذا": "why",
          "much": "amount", "many": "amount", "kam": "amount", "كام": "amount", "كم": "amount",
          "long": "duration", "قد": "duration"}
_STOP = {"how", "what", "can", "the", "a", "an", "is", "are", "do", "does", "i", "my", "to", "of", "for", "in", "on",
         "many", "much", "long", "please", "you", "me", "it", "be", "with", "and", "or", "at", "there", "any"}


def signature(question: str, lang: str, agent: str, version: str, access: tuple) -> str:
    norm = normalize(question)
    parts = [version, ",".join(sorted(access)), lang, agent,
             ",".join(sorted({m.group(1)[:5] for m in _AUD.finditer(norm)})),
             ",".join(sorted(_NUM.findall(norm.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))))),
             ",".join(sorted({QWORDS[w] for w in re.findall(r"\w+", norm) if w in QWORDS}))]  # where ≠ when
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _trigrams(text: str) -> Counter:
    words = [w for w in re.findall(r"\w+", normalize(text)) if w not in _STOP]
    grams = Counter()
    for w in tokenize(" ".join(words)) or words:
        w = f" {w} "
        grams.update(w[i:i + 3] for i in range(len(w) - 2))
    return grams


def _cos(a, b) -> float:
    if isinstance(a, Counter):
        dot = sum(v * b.get(k, 0) for k, v in a.items())
        na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    else:
        dot = sum(x * y for x, y in zip(a, b))
        na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def cacheable(answer) -> bool:
    return answer.mode == "answer" and not answer.degraded and not any(
        c.method in LIVE_METHODS or c.priority for _, c in answer.hits)


class SemanticCache:
    def __init__(self, threshold: float | None = None, size: int | None = None, ttl: float | None = None,
                 model: str | None = None, redis_url: str | None = None):
        self.threshold = threshold or float(os.getenv("AGENTKIT_SEMCACHE_THRESHOLD", "0.88"))
        self.size = size or int(os.getenv("AGENTKIT_SEMCACHE_SIZE", "2000"))
        self.ttl = ttl or float(os.getenv("AGENTKIT_CACHE_TTL", "3600"))
        self.lock = threading.Lock()
        self.buckets: OrderedDict[str, list] = OrderedDict()  # signature → [(ts, vector, question, answer)]
        self.encoder = None
        name = model if model is not None else os.getenv("AGENTKIT_SEMCACHE_MODEL", "")
        if name:
            from sentence_transformers import SentenceTransformer
            self.encoder = SentenceTransformer(name)
        url = redis_url if redis_url is not None else os.getenv("AGENTKIT_REDIS_URL", "")
        self.redis = None
        if url:
            import redis  # optional dependency: pip install redis
            self.redis = redis.Redis.from_url(url)

    def _vec(self, question: str):
        if self.encoder is not None:
            return [float(x) for x in self.encoder.encode([question], normalize_embeddings=True)[0]]
        return _trigrams(question)

    def get(self, question: str, sig: str):
        vec, now = self._vec(question), time.time()
        entries = self._load(sig)
        best = max(((self._sim(vec, e), e) for e in entries if now - e[0] < self.ttl), default=(0.0, None),
                   key=lambda x: x[0])
        if best[1] is not None and best[0] >= self.threshold:
            return best[1][3], best[0], best[1][4]
        return None, best[0], 0.0

    def put(self, question: str, sig: str, answer, cost: float = 0.0):
        entry = (time.time(), self._vec(question), question, answer, cost)
        if self.redis is not None:
            self.redis.rpush(f"agentkit:sem:{sig}", json.dumps(_dump(entry), ensure_ascii=False))
            self.redis.ltrim(f"agentkit:sem:{sig}", -50, -1)
            self.redis.expire(f"agentkit:sem:{sig}", int(self.ttl))
            return
        with self.lock:
            self.buckets.setdefault(sig, []).append(entry)
            self.buckets[sig] = self.buckets[sig][-50:]
            self.buckets.move_to_end(sig)
            while sum(len(v) for v in self.buckets.values()) > self.size:
                self.buckets.popitem(last=False)

    def clear(self):
        with self.lock:
            self.buckets.clear()

    def _sim(self, vec, entry) -> float:
        return _cos(vec, entry[1])

    def _load(self, sig: str) -> list:
        if self.redis is not None:
            return [_load(json.loads(x)) for x in self.redis.lrange(f"agentkit:sem:{sig}", 0, -1)]
        with self.lock:
            return list(self.buckets.get(sig, []))


def _dump(entry) -> dict:
    ts, vec, q, a, cost = entry
    return {"ts": ts, "vec": dict(vec) if isinstance(vec, Counter) else vec, "q": q, "answer": asdict(a), "cost": cost}


def _load(d: dict):
    from .chat import Answer
    from .rag import Chunk
    a = d["answer"]
    a["sources"] = [(n, Chunk(**c)) for n, c in a["sources"]]
    a["hits"] = [(s, Chunk(**c)) for s, c in a["hits"]]
    a["quotes"] = {int(k): v for k, v in a["quotes"].items()}
    vec = Counter(d["vec"]) if isinstance(d["vec"], dict) else d["vec"]
    return d["ts"], vec, d["q"], Answer(**a), d.get("cost", 0.0)
