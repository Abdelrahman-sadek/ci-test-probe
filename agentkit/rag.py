"""Sentence-aware chunking + hybrid retrieval.

Retrievers are fused with Reciprocal Rank Fusion (RRF, k=60):
  1. BM25 over light-stemmed words            (exact terms)
  2. TF-IDF over character 3-grams            (Arabic morphology, Franco-Arabic/transliteration noise)
  3. optional dense embeddings, e.g. BGE-M3   (`pip install agentkit[dense]`)
then an optional cross-encoder reranker (`[rerank]`, bge-reranker-v2-m3). Pure Python by default.
"""
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .arabic import detect_lang, normalize, sentences, tokenize, words
from .llm import LLM
from .ocr import extract

MAX_WORDS, OVERLAP_WORDS, RRF_K = 160, 30, 60
# OWASP LLM01/LLM04: instruction-like text inside documents is quarantined at ingestion, never retrieved.
INJECTION = re.compile(
    r"ignore (all |any |the |previous |prior |above )*(instructions|rules|prompts?)|disregard (all|the|previous|prior)"
    r"|system prompt|you are now|new instructions\s*:|developer mode|تجاهل (كل |جميع )?(التعليمات|الاوامر|الأوامر)",
    re.I)


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
    access: str = "public"
    flags: list[str] = field(default_factory=list)
    context: str = ""  # contextual-retrieval prefix written by the LLM (optional)
    tokens: list[str] = field(default_factory=list, repr=False)

    @property
    def indexed_text(self) -> str:
        return f"{self.context}\n{self.text}" if self.context else self.text


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
    """Pack whole sentences into windows of ≤ MAX_WORDS, carrying the last short sentence as overlap."""
    sents = []
    for s in sentences(text):
        w = s.split()
        sents += [" ".join(w[i:i + MAX_WORDS]) for i in range(0, len(w), MAX_WORDS)] if len(w) > MAX_WORDS else [s]
    out, cur, n = [], [], 0
    for s in sents:
        w = len(s.split())
        if cur and n + w > MAX_WORDS:
            out.append("\n".join(cur))
            cur = cur[-1:] if len(cur[-1].split()) <= OVERLAP_WORDS else []
            n = sum(len(x.split()) for x in cur)
        cur.append(s)
        n += w
    if cur:
        out.append("\n".join(cur))  # keep sentence/table-row boundaries for citable blocks
    return out


def chunk_document(path: Path, llm: LLM, contextualize: bool = False) -> list[Chunk]:
    chunks = []
    for page in extract(path, llm):
        meta, body = _frontmatter(page.text)
        title = meta.get("title", path.stem.replace("-", " ").title())
        source = meta.get("url", str(path))
        for section, sec_text in _sections(body):
            if len(sec_text.split()) < 6:  # skip banners/stubs
                continue
            for win in _windows(sec_text):
                header = f"{title} › {section}" if section else title
                text = f"{header}\n{win}"  # context header makes each chunk stand alone
                cid = hashlib.sha1(f"{source}|{page.page}|{text}".encode()).hexdigest()[:12]
                flags = ["injection"] if INJECTION.search(normalize(win)) else []
                chunks.append(Chunk(cid, text, title, section, source, page.page, detect_lang(win), page.method,
                                    meta.get("updated", ""), meta.get("access", "public"), flags))
        if contextualize and llm.live:  # Anthropic Contextual Retrieval: document cached, one call per chunk
            for c in chunks:
                if c.page == page.page and not c.context:
                    c.context = llm.contextualize(body, c.text)
    for c in chunks:
        c.tokens = tokenize(c.indexed_text)
    return chunks


def _grams(text: str, n: int = 3) -> Counter:
    g = Counter()
    for w in words(text):
        w = f" {w} "
        g.update(w[i:i + n] for i in range(max(len(w) - n + 1, 1)))
    return g


def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion: score(d) = Σ 1 / (k + rank)."""
    fused = defaultdict(float)
    for ranking in rankings:
        for rank, i in enumerate(ranking, 1):
            fused[i] += 1.0 / (k + rank)
    return fused


class Embedder:
    """Dense embeddings via sentence-transformers (default BAAI/bge-m3, best on Arabic RAG benchmarks)."""

    def __init__(self, model: str = ""):
        from sentence_transformers import SentenceTransformer
        self.name = model or os.getenv("AGENTKIT_EMBED_MODEL", "BAAI/bge-m3")
        self.model = SentenceTransformer(self.name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


class Reranker:
    """Cross-encoder reranker (default BAAI/bge-reranker-v2-m3, multilingual incl. Arabic)."""

    def __init__(self, model: str = ""):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(model or os.getenv("AGENTKIT_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"))

    def scores(self, query: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self.model.predict([(query, t) for t in texts])]


class Index:
    def __init__(self, chunks: list[Chunk] | None = None, embedder=None, reranker=None,
                 k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.embedder, self.reranker = embedder, reranker
        self.chunks: list[Chunk] = []
        self.vectors: list[list[float]] = []
        self.add(chunks or [])

    def add(self, chunks: list[Chunk]):
        seen = {c.id for c in self.chunks}
        new = [c for c in chunks if c.id not in seen]  # dedupe by content hash
        for c in new:
            c.tokens = c.tokens or tokenize(c.indexed_text)
        self.chunks += new
        if self.embedder and new:
            self.vectors += self.embedder.embed([c.indexed_text for c in new])
        self._build()

    def _build(self):
        n = len(self.chunks)
        self.tf = [Counter(c.tokens) for c in self.chunks]
        self.lens = [len(c.tokens) for c in self.chunks]
        self.avgdl = sum(self.lens) / max(n, 1)
        self.df, self.postings = Counter(), defaultdict(list)
        for i, tf in enumerate(self.tf):
            self.df.update(tf.keys())
            for t in tf:
                self.postings[t].append(i)
        grams = [_grams(c.indexed_text) for c in self.chunks]
        gdf = Counter(g for gc in grams for g in gc)
        self.gidf = {g: math.log((n + 1) / (d + 1)) + 1 for g, d in gdf.items()}
        self.gpostings = defaultdict(list)
        for i, gc in enumerate(grams):
            vec = {g: tf * self.gidf[g] for g, tf in gc.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            for g, v in vec.items():
                self.gpostings[g].append((i, v / norm))

    def _bm25(self, query: str, allowed: set[int], pool: int) -> list[int]:
        n, scores = len(self.chunks), defaultdict(float)
        for t in set(tokenize(query)):
            idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i in self.postings.get(t, ()):
                if i in allowed:
                    tf = self.tf[i][t]
                    scores[i] += idf * tf * (self.k1 + 1) / (
                        tf + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avgdl))
        return sorted(scores, key=lambda i: -scores[i])[:pool]

    def _chargram(self, query: str, allowed: set[int], pool: int) -> list[int]:
        q = {g: tf * self.gidf[g] for g, tf in _grams(query).items() if g in self.gidf}
        norm = math.sqrt(sum(v * v for v in q.values())) or 1.0
        scores = defaultdict(float)
        for g, v in q.items():
            for i, w in self.gpostings[g]:
                if i in allowed:
                    scores[i] += v / norm * w
        return [i for i in sorted(scores, key=lambda i: -scores[i]) if scores[i] >= 0.05][:pool]

    def _dense(self, query: str, allowed: set[int], pool: int) -> list[int]:
        qv = self.embedder.embed([query])[0]
        scores = {i: sum(a * b for a, b in zip(qv, self.vectors[i])) for i in allowed}
        return sorted(scores, key=lambda i: -scores[i])[:pool]

    def search(self, query: str, k: int = 5, expansions: list[str] | None = None,
               access: tuple[str, ...] = ("public",), pool: int = 50) -> list[tuple[float, Chunk]]:
        """Hybrid search. Only chunks the caller may see (OWASP LLM08) and never quarantined ones."""
        q = " ".join([query, *(expansions or [])])
        allowed = {i for i, c in enumerate(self.chunks) if c.access in access and "injection" not in c.flags}
        rankings = [self._bm25(q, allowed, pool), self._chargram(q, allowed, pool)]
        if self.embedder and len(self.vectors) == len(self.chunks):
            rankings.append(self._dense(q, allowed, pool))
        fused = rrf(rankings)
        top = sorted(fused, key=lambda i: -fused[i])[: max(k, 20) if self.reranker else k]
        if self.reranker and top:
            rs = self.reranker.scores(query, [self.chunks[i].indexed_text for i in top])
            order = sorted(range(len(top)), key=lambda j: -rs[j])
            return [(rs[j], self.chunks[top[j]]) for j in order[:k]]
        return [(fused[i], self.chunks[i]) for i in top]

    def save(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 2, "chunks": [asdict(c) for c in self.chunks],
                "embed_model": getattr(self.embedder, "name", ""), "vectors": self.vectors}
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, embedder=None, reranker=None) -> "Index":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data["chunks"]  # v1 indexes were a bare list
        index = cls(reranker=reranker)
        index.chunks = [Chunk(**d) for d in rows]
        if embedder and isinstance(data, dict) and len(data.get("vectors", [])) == len(index.chunks):
            index.embedder, index.vectors = embedder, data["vectors"]
        index._build()
        return index


def ingest(paths: list[str | Path], llm: LLM, index: Index | None = None,
           contextualize: bool = False) -> tuple[Index, list[dict]]:
    index = index or Index()
    report = []
    files = [f for p in map(Path, paths) for f in (sorted(p.rglob("*")) if p.is_dir() else [p])
             if f.is_file() and not f.name.startswith((".", "README"))]
    for f in files:
        try:
            chunks = chunk_document(f, llm, contextualize)
            index.add(chunks)
            report.append({"source": str(f), "chunks": len(chunks),
                           "methods": sorted({c.method for c in chunks}),
                           "quarantined": sum("injection" in c.flags for c in chunks), "error": ""})
        except ValueError as e:
            report.append({"source": str(f), "chunks": 0, "methods": [], "quarantined": 0, "error": str(e)})
    return index, report


__all__ = ["Chunk", "Embedder", "Index", "Reranker", "chunk_document", "ingest", "rrf"]
