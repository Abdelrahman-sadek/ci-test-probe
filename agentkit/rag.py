"""Structure-aware chunking + bilingual BM25 index (pure Python, JSON on disk)."""
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .arabic import detect_lang, normalize, tokenize
from .llm import LLM
from .ocr import extract

MAX_WORDS, OVERLAP_WORDS = 220, 30


@dataclass
class Chunk:
    id: str
    text: str
    title: str
    section: str
    source: str
    page: int
    lang: str
    method: str
    updated: str = ""
    tokens: list[str] = field(default_factory=list, repr=False)


def _frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in meta.items()}, m.group(2)


def _sections(text: str) -> list[tuple[str, str]]:
    """Split on Markdown headings; fall back to one section."""
    parts, heading, buf = [], "", []
    for line in text.splitlines():
        if re.match(r"^#{1,6} ", line):
            if "".join(buf).strip():
                parts.append((heading, "\n".join(buf)))
            heading, buf = line.lstrip("# ").strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        parts.append((heading, "\n".join(buf)))
    return parts


def _windows(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, cur = [], []
    for p in paras:
        words = p.split()
        if cur and len(cur) + len(words) > MAX_WORDS:
            out.append(" ".join(cur))
            cur = cur[-OVERLAP_WORDS:]
        cur.extend(words)
        while len(cur) > MAX_WORDS:
            out.append(" ".join(cur[:MAX_WORDS]))
            cur = cur[MAX_WORDS - OVERLAP_WORDS:]
    if cur:
        out.append(" ".join(cur))
    return out


def chunk_document(path: Path, llm: LLM) -> list[Chunk]:
    chunks = []
    for page in extract(path, llm):
        meta, body = _frontmatter(page.text)
        title = meta.get("title", path.stem.replace("-", " ").title())
        source = meta.get("url", str(path))
        for section, sec_text in _sections(body):
            if len(sec_text.split()) < 8:  # skip banners/stubs
                continue
            for win in _windows(sec_text):
                header = f"{title} › {section}" if section else title
                text = f"{header}\n{win}"  # context header makes each chunk stand alone
                cid = hashlib.sha1(f"{source}|{page.page}|{text}".encode()).hexdigest()[:12]
                chunks.append(Chunk(cid, text, title, section, source, page.page, detect_lang(win),
                                    page.method, meta.get("updated", ""), tokenize(text)))
    return chunks


class Index:
    def __init__(self, chunks: list[Chunk] | None = None, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.chunks: list[Chunk] = []
        self.add(chunks or [])

    def add(self, chunks: list[Chunk]):
        seen = {c.id for c in self.chunks}
        self.chunks += [c for c in chunks if c.id not in seen]  # dedupe by content hash
        self.df = Counter(t for c in self.chunks for t in set(c.tokens))
        self.avgdl = sum(len(c.tokens) for c in self.chunks) / max(len(self.chunks), 1)

    def search(self, query: str, k: int = 5, expansions: list[str] | None = None) -> list[tuple[float, Chunk]]:
        q = tokenize(query)
        for e in expansions or []:
            q += tokenize(e)
        n = len(self.chunks)
        scored = []
        for c in self.chunks:
            tf = Counter(c.tokens)
            s = 0.0
            for t in set(q):
                if t in tf:
                    idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * len(c.tokens) / self.avgdl))
            if s > 0:
                scored.append((s, c))
        return sorted(scored, key=lambda x: -x[0])[:k]

    def save(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps([asdict(c) for c in self.chunks], ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Index":
        return cls([Chunk(**d) for d in json.loads(Path(path).read_text(encoding="utf-8"))])


def ingest(paths: list[str | Path], llm: LLM, index: Index | None = None) -> tuple[Index, list[dict]]:
    index = index or Index()
    report = []
    files = [f for p in map(Path, paths) for f in (sorted(p.rglob("*")) if p.is_dir() else [p]) if f.is_file()]
    for f in files:
        try:
            chunks = chunk_document(f, llm)
            index.add(chunks)
            report.append({"source": str(f), "chunks": len(chunks),
                           "methods": sorted({c.method for c in chunks}), "error": ""})
        except ValueError as e:
            report.append({"source": str(f), "chunks": 0, "methods": [], "error": str(e)})
    return index, report


__all__ = ["Chunk", "Index", "chunk_document", "ingest", "normalize"]
