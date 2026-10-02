"""Answer cache: repeated questions ("library hours?") are answered without retrieval or an LLM call.

Keys include the index version and the caller's access levels, so a cached answer is invalidated the
moment the corpus changes and is never served to someone with different permissions (OWASP LLM08).
Follow-up questions (with history) are never cached.
"""
import hashlib
import os
import threading
import time
from collections import OrderedDict

from .arabic import normalize


class AnswerCache:
    def __init__(self, size: int | None = None, ttl: float | None = None):
        self.size = size or int(os.getenv("AGENTKIT_CACHE_SIZE", "2000"))
        self.ttl = ttl if ttl is not None else float(os.getenv("AGENTKIT_CACHE_TTL", "3600"))
        self.data: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self.lock = threading.Lock()
        self.hits = self.misses = 0

    @staticmethod
    def key(question: str, version: str, access: tuple) -> str:
        norm = " ".join(normalize(question).split()).strip(" ?؟!.")
        return hashlib.sha256(f"{version}|{','.join(sorted(access))}|{norm}".encode()).hexdigest()

    def get(self, key: str):
        with self.lock:
            item = self.data.get(key)
            if item and time.monotonic() - item[0] < self.ttl:
                self.data.move_to_end(key)
                self.hits += 1
                return item[1]
            if item:
                del self.data[key]
            self.misses += 1
            return None

    def put(self, key: str, value):
        with self.lock:
            self.data[key] = (time.monotonic(), value)
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
