"""Sentence-aware chunking, safe ingestion with provenance, and hybrid retrieval.

Retrievers are fused with Reciprocal Rank Fusion (RRF, k=60):
  1. BM25 over light-stemmed words            (exact terms)
  2. character 3-grams                        (Arabic morphology, Franco-Arabic/transliteration noise)
  3. optional dense embeddings, e.g. BGE-M3   (`pip install agentkit[dense]`)
then an optional cross-encoder reranker (`[rerank]`, bge-reranker-v2-m3).

Two back ends share this module's `BaseIndex` contract:
  * `Index`        — in-memory, saved as JSON; zero dependencies, good to ~10k chunks.
  * `SqliteIndex`  — SQLite FTS5 (BM25 + trigram tables) on disk; see `agentkit/store_sqlite.py`.
"""
import hashlib
import json
import math
import os
import re
import threading
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .arabic import detect_lang, normalize, sentences, tokenize, words
from .llm import LLM
from .ocr import LOW_CONFIDENCE, extract, safe_extract

MAX_WORDS, OVERLAP_WORDS, RRF_K = 160, 30, 60
BLOCKING_FLAGS = {"injection", "unreviewed"}  # chunks with these flags are never retrieved
# OWASP LLM01/LLM04: instruction-like or exfiltrating text inside documents is quarantined at ingestion.
INJECTION = re.compile(
    r"(ignore|disregard|forget|override) (all |any |the |your |previous |prior |above |earlier )*"
    r"(instructions|rules|prompts?|guidelines)"
    r"|system prompt|you are now|new instructions\s*:|developer mode|jailbreak|act as (an? )?(unrestricted|dan)"
    r"|!\[[^\]]*\]\(https?://|<script|javascript:"
    r"|(تجاهل|انس[يى]|اهمل) (كل |جميع )?(التعليمات|الاوامر|القواعد)|موجه النظام|ignore el ta3limat",
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
    origin: str = ""   # the ingested file this chunk came from (provenance)
    confidence: float = 1.0  # extraction/OCR confidence of the page
    valid_from: str = ""     # ISO date: not shown before this (e.g. Ramadan hours)
    valid_to: str = ""       # ISO date: hidden after this (expired policy or notice)
    priority: str = ""       # "urgent" → pinned above other results when relevant
    meta: dict = field(default_factory=dict)  # bibliographic fields (type, author, year, advisor, department…)

    def current(self, today: str) -> bool:
        return (not self.valid_from or self.valid_from <= today) and (not self.valid_to or today <= self.valid_to)

    @property
    def indexed_text(self) -> str:
        return f"{self.context}\n{self.text}" if self.context else self.text

    @property
    def blocked(self) -> bool:
        return bool(BLOCKING_FLAGS & set(self.flags))


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


def chunk_document(path: Path, llm: LLM, safe: bool = True) -> tuple[list[Chunk], dict[str, str]]:
    """Return the chunks of one file and, per chunk id, the page text it came from (for contextualising)."""
    chunks, bodies = [], {}
    pages = safe_extract(path, llm) if safe else extract(path, llm)
    fixes_file = path.with_name(path.name + ".corrections.json")  # staff-corrected page text wins over OCR
    if fixes_file.exists():
        from .ocr import Page
        fixes = {int(k): v for k, v in json.loads(fixes_file.read_text(encoding="utf-8")).items()}
        pages = [Page(fixes[pg.page], pg.page, "corrected", 1.0) if pg.page in fixes else pg for pg in pages]
    sidecar = path.with_name(path.name + ".meta.json")  # title/url/access for PDFs and images
    side = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    biblio = side.pop("biblio", {}) if isinstance(side.get("biblio"), dict) else {}  # thesis/catalog metadata
    who = "; ".join(biblio["author"]) if isinstance(biblio.get("author"), list) else biblio.get("author", "")
    context = ", ".join(x for x in (f"by {who}" if who else "", biblio.get("degree", ""), biblio.get("department", ""),
                                    biblio.get("year", ""), f"advisor {biblio['advisor']}" if biblio.get("advisor") else "")
                        if x)  # every chunk of a thesis names its author, department and year
    for page in pages:
        meta, body = _frontmatter(page.text)
        meta = {**{k: str(v) for k, v in side.items()}, **meta}
        title = meta.get("title", path.stem.replace("-", " ").title())
        source = meta.get("url", str(path))
        for section, sec_text in _sections(body):
            if len(sec_text.split()) < 6:  # skip banners/stubs
                continue
            for win in _windows(sec_text):
                header = f"{title} › {section}" if section else title
                text = f"{header}\n{win}"  # context header makes each chunk stand alone
                cid = hashlib.sha1(f"{source}|{page.page}|{text}".encode(), usedforsecurity=False).hexdigest()[:12]
                flags = ["injection"] if INJECTION.search(normalize(win)) else []
                c = Chunk(cid, text, title, section, source, page.page, detect_lang(win), page.method,
                          meta.get("updated", ""), meta.get("access", "public"), flags, origin=str(path),
                          confidence=page.confidence, valid_from=meta.get("valid_from", ""),
                          valid_to=meta.get("valid_to", ""), priority=meta.get("priority", ""), meta=dict(biblio),
                          context=f"{title} — {context}" if context else "")
                chunks.append(c)
                bodies[cid] = body
    for c in chunks:
        c.tokens = tokenize(c.indexed_text)
    chunk_document.low_pages = sorted({(pg.page, pg.confidence) for pg in pages if pg.confidence < LOW_CONFIDENCE})
    return chunks, bodies


def _grams(text: str, n: int = 3) -> Counter:
    g = Counter()
    for w in words(text):
        w = f" {w} "
        g.update(w[i:i + n] for i in range(max(len(w) - n + 1, 1)))
    return g


def rrf(rankings: list[list], k: int = RRF_K) -> dict:
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


class BaseIndex:
    """Shared contract and fusion logic. Back ends implement the `_rank_*` hooks and storage methods.
    Writes and reads are serialised with a re-entrant lock, so background ingestion is safe while serving."""

    def __init__(self, embedder=None, reranker=None):
        self.embedder, self.reranker = embedder, reranker
        self.lock = threading.RLock()

    # Storage contract
    def add(self, chunks: list[Chunk]): raise NotImplementedError

    def iter_chunks(self):
        raise NotImplementedError

    def vocabulary(self) -> list[str]:
        """Distinct index terms, cached per index version (spelling correction in query rewriting)."""
        if getattr(self, "_vocab_version", None) != self.version:
            self._vocab = sorted({t for c in self.iter_chunks() if not c.blocked for t in c.tokens})
            self._vocab_version = self.version
        return self._vocab
    def remove_origin(self, origin: str) -> int: raise NotImplementedError
    def set_flag(self, origin: str, flag: str, on: bool) -> int: raise NotImplementedError
    def get(self, ids: list) -> list[Chunk]: raise NotImplementedError
    def manifest(self) -> dict[str, dict]: raise NotImplementedError
    def manifest_set(self, origin: str, entry: dict | None): raise NotImplementedError
    def save(self, path: str | Path): raise NotImplementedError

    @property
    def size(self) -> int: raise NotImplementedError

    @property
    def version(self) -> str:
        """Changes whenever the searchable content changes (cache keys depend on it)."""
        raise NotImplementedError

    def _rank_bm25(self, query: str, access: tuple, pool: int) -> list: raise NotImplementedError
    def _rank_chargram(self, query: str, access: tuple, pool: int) -> list: raise NotImplementedError

    def _rank_dense(self, query: str, access: tuple, pool: int) -> list:
        return []

    # Snapshots
    def snapshot(self, path: str | Path, keep: int | None = None) -> Path | None:
        """Copy the saved index to <path>.snapshots/<timestamp> before a change; keep the newest N (default 5)."""
        import shutil
        import time
        path = Path(path)
        if not path.exists() or self.size == 0:  # nothing worth restoring
            return None
        keep = keep or int(os.getenv("AGENTKIT_SNAPSHOTS", "5"))
        d = path.with_name(path.name + ".snapshots")
        d.mkdir(exist_ok=True)
        dest = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**9:09d}{path.suffix}"  # sortable
        if hasattr(self, "db"):  # SQLite: consistent online backup
            import sqlite3
            with sqlite3.connect(dest) as out:
                self.db.backup(out)
        else:
            shutil.copy2(path, dest)
        for old in sorted(d.iterdir())[:-keep]:
            old.unlink()
        return dest

    # Search
    _filter: dict | None = None  # metadata filter for the search in progress (set under self.lock)

    def search(self, query: str, k: int = 5, expansions: list[str] | None = None,
               access: tuple[str, ...] = ("public",), pool: int = 50,
               filters: dict | None = None) -> list[tuple[float, Chunk]]:
        """Hybrid search over chunks the caller may see (OWASP LLM08); quarantined chunks never surface.
        `filters` narrows by metadata: type, department, advisor, year_from, year_to (e.g. theses)."""
        import datetime
        q = " ".join([query, *(expansions or [])])
        today = datetime.date.today().isoformat()
        filters = {k_: v for k_, v in (filters or {}).items() if v not in (None, "")} or None
        with self.lock:
            self._filter = filters
            try:
                rankings = [self._rank_bm25(q, access, pool), self._rank_chargram(q, access, pool)]
                if self.embedder:
                    rankings.append(self._rank_dense(q, access, pool))
            finally:
                self._filter = None
            fused = rrf([r for r in rankings if r])
            ranked = sorted(fused, key=lambda i: -fused[i])[: max(k, 20) * 2]
            chunks = dict(zip(ranked, self.get(ranked)))
            # effective dates: expired or not-yet-valid content never surfaces
            top = [i for i in ranked if chunks[i].current(today) and (not filters or meta_match(chunks[i].meta, filters))
                   ][: max(k, 20) if self.reranker else k]
        if self.reranker and top:
            rs = self.reranker.scores(query, [chunks[i].indexed_text for i in top])
            order = sorted(range(len(top)), key=lambda j: -rs[j])
            return [(rs[j], chunks[top[j]]) for j in order[:k]]
        return [(fused[i], chunks[i]) for i in top]


class Index(BaseIndex):
    """In-memory index persisted as JSON."""

    def __init__(self, chunks: list[Chunk] | None = None, embedder=None, reranker=None,
                 k1: float = 1.5, b: float = 0.75):
        super().__init__(embedder, reranker)
        self.k1, self.b = k1, b
        self.chunks: list[Chunk] = []
        self.vectors: list[list[float]] = []
        self._manifest: dict[str, dict] = {}
        self._version = uuid.uuid4().hex
        self.add(chunks or [])

    @property
    def size(self) -> int:
        return len(self.chunks)

    @property
    def version(self) -> str:
        return self._version

    def iter_chunks(self):
        return iter(self.chunks)

    def add(self, chunks: list[Chunk]):
        with self.lock:
            seen = {c.id for c in self.chunks}
            new = [c for c in chunks if c.id not in seen]  # dedupe by content hash
            for c in new:
                c.tokens = c.tokens or tokenize(c.indexed_text)
            self.chunks += new
            if self.embedder and new:
                self.vectors += self.embedder.embed([c.indexed_text for c in new])
            self._build()

    def remove_origin(self, origin: str) -> int:
        with self.lock:
            keep = [i for i, c in enumerate(self.chunks) if c.origin != origin]
            removed = len(self.chunks) - len(keep)
            if removed:
                self.chunks = [self.chunks[i] for i in keep]
                if self.vectors:
                    self.vectors = [self.vectors[i] for i in keep]
                self._build()
            return removed

    def set_flag(self, origin: str, flag: str, on: bool) -> int:
        with self.lock:
            n = 0
            for c in self.chunks:
                if c.origin == origin and (flag in c.flags) != on:
                    c.flags = c.flags + [flag] if on else [f for f in c.flags if f != flag]
                    n += 1
            self._version = uuid.uuid4().hex
            return n

    def get(self, ids: list) -> list[Chunk]:
        return [self.chunks[i] for i in ids]

    def manifest(self) -> dict[str, dict]:
        return dict(self._manifest)

    def manifest_set(self, origin: str, entry: dict | None):
        with self.lock:
            if entry is None:
                self._manifest.pop(origin, None)
            else:
                self._manifest[origin] = entry

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
        self._version = uuid.uuid4().hex

    def _allowed(self, access: tuple) -> set[int]:
        f = self._filter
        return {i for i, c in enumerate(self.chunks) if c.access in access and not c.blocked
                and (f is None or meta_match(c.meta, f))}

    def _rank_bm25(self, query, access, pool):
        allowed, n, scores = self._allowed(access), len(self.chunks), defaultdict(float)
        for t in set(tokenize(query)):
            idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i in self.postings.get(t, ()):
                if i in allowed:
                    tf = self.tf[i][t]
                    scores[i] += idf * tf * (self.k1 + 1) / (
                        tf + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avgdl))
        return sorted(scores, key=lambda i: -scores[i])[:pool]

    def _rank_chargram(self, query, access, pool):
        allowed = self._allowed(access)
        q = {g: tf * self.gidf[g] for g, tf in _grams(query).items() if g in self.gidf}
        norm = math.sqrt(sum(v * v for v in q.values())) or 1.0
        scores = defaultdict(float)
        for g, v in q.items():
            for i, w in self.gpostings[g]:
                if i in allowed:
                    scores[i] += v / norm * w
        return [i for i in sorted(scores, key=lambda i: -scores[i]) if scores[i] >= 0.05][:pool]

    def _rank_dense(self, query, access, pool):
        if len(self.vectors) != len(self.chunks):
            return []
        qv = self.embedder.embed([query])[0]
        scores = {i: sum(a * b for a, b in zip(qv, self.vectors[i])) for i in self._allowed(access)}
        return sorted(scores, key=lambda i: -scores[i])[:pool]

    def save(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            data = {"version": 3, "chunks": [asdict(c) for c in self.chunks], "manifest": self._manifest,
                    "embed_model": getattr(self.embedder, "name", ""), "vectors": self.vectors}
        tmp = Path(f"{path}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # atomic: readers never see a half-written index

    @classmethod
    def load(cls, path: str | Path, embedder=None, reranker=None) -> "Index":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data["chunks"]  # v1 indexes were a bare list
        index = cls(reranker=reranker)
        index.chunks = [Chunk(**d) for d in rows]
        index._manifest = {} if isinstance(data, list) else data.get("manifest", {})
        if embedder and isinstance(data, dict) and len(data.get("vectors", [])) == len(index.chunks):
            index.embedder, index.vectors = embedder, data["vectors"]
        index._build()
        return index


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def ingest(paths: list[str | Path], llm: LLM, index: BaseIndex | None = None, contextualize: str | bool = False,
           review: bool = False, allowed: list[str] | None = None, safe: bool = True,
           force: bool = False, workers: int = 1, checkpoint=None, checkpoint_every: int = 25
           ) -> tuple[BaseIndex, list[dict]]:
    """Incremental, provenance-tracked ingestion.

    * unchanged files (same SHA-256) are skipped; changed files replace their old chunks;
    * with `review=True`, chunks of a *changed* source are held as `unreviewed` until `approve()` (LLM04);
    * web sources must be on the domain allowlist (`config/allowed-domains.txt`);
    * files are parsed in a sandboxed child process with size/page/time/memory limits;
    * `contextualize="sync"|"batch"` adds Contextual Retrieval prefixes (live mode; batch = 50% cheaper).
    """
    from .security import source_allowed
    index = index if index is not None else Index()
    report, staged = [], []
    files = [f for p in map(Path, paths) for f in (sorted(p.rglob("*")) if p.is_dir() else [p])
             if f.is_file() and not f.name.startswith((".", "README"))
             and not f.name.endswith((".meta.json", ".corrections.json"))]
    known = index.manifest()

    def parse(f: Path):
        origin = str(f)
        row = {"source": origin, "chunks": 0, "methods": [], "quarantined": 0, "status": "", "error": "",
               "low_confidence_pages": []}
        try:
            sha = _sha256(f)
            prev = known.get(origin)
            if prev and prev.get("sha256") == sha and not force:
                row["status"] = "unchanged"
                return row, None
            chunks, bodies = chunk_document(f, llm, safe)
            bad = sorted({c.source for c in chunks if not source_allowed(c.source, allowed)})
            if bad:
                raise ValueError(f"source domain not on the allowlist: {', '.join(bad)}")
            held = review and prev is not None
            if held:
                for c in chunks:
                    c.flags.append("unreviewed")
            low = [{"page": pg, "confidence": conf} for pg, conf in getattr(chunk_document, "low_pages", [])]
            row.update(chunks=len(chunks), methods=sorted({c.method for c in chunks}), low_confidence_pages=low,
                       quarantined=sum("injection" in c.flags for c in chunks),
                       status="pending-review" if held else ("updated" if prev else "new"))
            return row, (origin, sha, chunks, bodies, "pending" if held else "approved",
                         chunks[0].source if chunks else "", low)
        except ValueError as e:  # includes ocr.ParseError
            row["error"] = str(e)
            return row, None

    if workers > 1:  # each parse runs in its own sandboxed child process, so threads give real parallelism
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(parse, files))
    else:
        results = [parse(f) for f in files]
    for row, item in results:
        report.append(row)
        if item:
            staged.append(item)
    if contextualize and llm.live:
        pairs = [(bodies[c.id], c) for _, _, chunks, bodies, *_ in staged for c in chunks if not c.context]
        if contextualize == "batch":
            contexts = llm.contextualize_batch([(doc, c.text) for doc, c in pairs])
        else:
            contexts = [llm.contextualize(doc, c.text) for doc, c in pairs]
        for (_, c), ctx in zip(pairs, contexts):
            c.context = ctx
            c.tokens = tokenize(c.indexed_text)
    import time
    for n, (origin, sha, chunks, _, status, url, low) in enumerate(staged, 1):
        with index.lock:
            index.remove_origin(origin)
            index.add(chunks)
            index.manifest_set(origin, {"sha256": sha, "status": status, "url": url, "chunks": len(chunks),
                                        "low_confidence_pages": low, "ingested_at": time.time()})
        if checkpoint and n % checkpoint_every == 0:  # long archive runs survive interruption
            checkpoint(index)
    return index, report


def freshness(index: BaseIndex, appdb=None, stale_days: float | None = None, now: float | None = None) -> dict:
    """Sources not re-ingested for AGENTKIT_STALE_DAYS (default 120) and notices expiring within 3 days."""
    import datetime
    import time
    now = now or time.time()
    stale_days = stale_days if stale_days is not None else float(os.getenv("AGENTKIT_STALE_DAYS", "120"))
    stale = [{"origin": o, "url": e.get("url", ""), "days": round((now - e.get("ingested_at", 0)) / 86400)}
             for o, e in index.manifest().items() if now - e.get("ingested_at", 0) > stale_days * 86400]
    soon = (datetime.date.fromtimestamp(now) + datetime.timedelta(days=3)).isoformat()
    expiring = [n for n in (appdb.notices() if appdb else []) if n["valid_to"] and n["valid_to"] <= soon]
    return {"stale_sources": stale, "expiring_notices": expiring}


def meta_match(meta: dict, f: dict) -> bool:
    """Does a chunk's metadata satisfy a filter? Text fields match case-insensitively by substring."""
    for key in ("type", "department", "degree", "language"):
        if f.get(key) and f[key].lower() not in str(meta.get(key, "")).lower():
            return False
    for key in ("advisor", "author"):  # names in any order: "Sara Ibrahim" matches "Ibrahim, Sara"
        if f.get(key):
            have = set(re.findall(r"\w+", normalize(str(meta.get(key, "")))))
            if not set(re.findall(r"\w+", normalize(f[key]))) <= have:
                return False
    year = str(meta.get("year", ""))[:4]
    if f.get("year_from") or f.get("year_to"):
        if not year.isdigit():
            return False
        if f.get("year_from") and int(year) < int(f["year_from"]):
            return False
        if f.get("year_to") and int(year) > int(f["year_to"]):
            return False
    return True


def approve(index: BaseIndex, origin: str | None = None) -> list[str]:
    """Release sources held for review (all pending ones when origin is None). Returns approved origins."""
    done = []
    for o, entry in index.manifest().items():
        if entry.get("status") == "pending" and origin in (None, o):
            index.set_flag(o, "unreviewed", False)
            index.manifest_set(o, {**entry, "status": "approved"})
            done.append(o)
    return done


__all__ = ["BaseIndex", "Chunk", "Embedder", "Index", "Reranker", "approve", "chunk_document", "freshness",
           "ingest", "rrf"]
