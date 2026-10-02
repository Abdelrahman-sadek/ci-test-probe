"""On-disk index: SQLite FTS5 for BM25 (pre-stemmed words) and the built-in `trigram` tokenizer for
character matching. Nothing to install, safe for concurrent readers (WAL), and fast at 100k+ chunks.
Optional dense vectors go to a vector store adapter (e.g. Qdrant, `agentkit/vector.py`).

Select it by giving the index a `.db`/`.sqlite` path: `agentkit --index data/index.db ingest …`.
"""
import json
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path

from .arabic import tokenize, words
from .rag import BaseIndex, Chunk

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS chunks(rowid INTEGER PRIMARY KEY, id TEXT UNIQUE, origin TEXT, access TEXT,
                                  blocked INTEGER DEFAULT 0, data TEXT);
CREATE INDEX IF NOT EXISTS chunks_origin ON chunks(origin);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_words USING fts5(body, tokenize='unicode61 remove_diacritics 0');
CREATE VIRTUAL TABLE IF NOT EXISTS fts_grams USING fts5(body, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS vocab_words USING fts5vocab(fts_words, 'row');
CREATE VIRTUAL TABLE IF NOT EXISTS vocab_grams USING fts5vocab(fts_grams, 'row');
CREATE TABLE IF NOT EXISTS manifest(origin TEXT PRIMARY KEY, entry TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


class SqliteIndex(BaseIndex):
    def __init__(self, path: str | Path, embedder=None, reranker=None, vectors=None):
        super().__init__(embedder, reranker)
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.vectors = vectors  # optional vector store adapter (upsert/delete/search)

    # Storage
    def _bump(self):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('version', ?)", (uuid.uuid4().hex,))

    @property
    def version(self) -> str:
        row = self.db.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        return row[0] if row else "empty"

    @property
    def size(self) -> int:
        return self.db.execute("SELECT count(*) FROM chunks").fetchone()[0]

    def add(self, chunks: list[Chunk]):
        with self.lock, self.db:
            new = []
            for c in chunks:
                c.tokens = c.tokens or tokenize(c.indexed_text)
                cur = self.db.execute("INSERT OR IGNORE INTO chunks(id, origin, access, blocked, data) "
                                      "VALUES (?,?,?,?,?)", (c.id, c.origin, c.access, int(c.blocked),
                                                             json.dumps(asdict(c), ensure_ascii=False)))
                if cur.rowcount:
                    rid = cur.lastrowid
                    self.db.execute("INSERT INTO fts_words(rowid, body) VALUES (?,?)", (rid, " ".join(c.tokens)))
                    self.db.execute("INSERT INTO fts_grams(rowid, body) VALUES (?,?)",
                                    (rid, " ".join(words(c.indexed_text))))
                    new.append((rid, c))
            if self.vectors is not None and self.embedder and new:
                vecs = self.embedder.embed([c.indexed_text for _, c in new])
                self.vectors.upsert([rid for rid, _ in new], vecs, [c.access for _, c in new])
            self._bump()

    def remove_origin(self, origin: str) -> int:
        with self.lock, self.db:
            rids = [r[0] for r in self.db.execute("SELECT rowid FROM chunks WHERE origin=?", (origin,))]
            for rid in rids:
                self.db.execute("DELETE FROM fts_words WHERE rowid=?", (rid,))
                self.db.execute("DELETE FROM fts_grams WHERE rowid=?", (rid,))
            self.db.execute("DELETE FROM chunks WHERE origin=?", (origin,))
            if self.vectors is not None and rids:
                self.vectors.delete(rids)
            if rids:
                self._bump()
            return len(rids)

    def set_flag(self, origin: str, flag: str, on: bool) -> int:
        with self.lock, self.db:
            n = 0
            for rid, data in self.db.execute("SELECT rowid, data FROM chunks WHERE origin=?", (origin,)).fetchall():
                c = Chunk(**json.loads(data))
                if (flag in c.flags) == on:
                    continue
                c.flags = c.flags + [flag] if on else [f for f in c.flags if f != flag]
                self.db.execute("UPDATE chunks SET data=?, blocked=? WHERE rowid=?",
                                (json.dumps(asdict(c), ensure_ascii=False), int(c.blocked), rid))
                n += 1
            self._bump()
            return n

    def get(self, ids: list) -> list[Chunk]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))  # only placeholders are interpolated; values are bound
        rows = dict(self.db.execute(f"SELECT rowid, data FROM chunks WHERE rowid IN ({marks})", ids).fetchall())  # nosec B608
        return [Chunk(**json.loads(rows[i])) for i in ids]

    def iter_chunks(self):
        return (Chunk(**json.loads(d)) for (d,) in self.db.execute("SELECT data FROM chunks"))

    def vocabulary(self) -> list[str]:
        if getattr(self, "_vocab_version", None) != self.version:
            self._vocab = [r[0] for r in self.db.execute("SELECT term FROM vocab_words ORDER BY term")]
            self._vocab_version = self.version
        return self._vocab

    def manifest(self) -> dict[str, dict]:
        return {o: json.loads(e) for o, e in self.db.execute("SELECT origin, entry FROM manifest")}

    def manifest_set(self, origin: str, entry: dict | None):
        with self.lock, self.db:
            if entry is None:
                self.db.execute("DELETE FROM manifest WHERE origin=?", (origin,))
            else:
                self.db.execute("INSERT OR REPLACE INTO manifest VALUES (?,?)", (origin, json.dumps(entry)))

    def save(self, path: str | Path = ""):
        self.db.commit()  # already durable; kept for interface parity with the JSON index

    # Ranking
    def _selective(self, vocab: str, terms: list[str], keep: int, max_df: float = 0.2) -> list[str]:
        """Keep the rarest terms: very common ones add little to BM25 but force scoring most of the table."""
        if len(terms) <= 3:
            return terms
        marks = ",".join("?" * len(terms))
        df = dict(self.db.execute(f"SELECT term, doc FROM {vocab} WHERE term IN ({marks})", terms).fetchall())  # nosec B608
        n = max(self.size, 1)
        ranked = sorted((t for t in terms if t in df), key=lambda t: df[t])
        selective = [t for t in ranked if df[t] <= max_df * n] or ranked[:3]
        return selective[:keep]

    def _ranked(self, table: str, terms: list[str], access: tuple, pool: int) -> list[int]:
        if not terms:
            return []
        if table not in ("fts_words", "fts_grams"):
            raise ValueError(table)
        match = " OR ".join(map(_quote, terms))
        # Fast path: let FTS5 rank and cut first (ORDER BY rank LIMIT), then filter access/quarantine.
        cand = [r[0] for r in self.db.execute(
            f"SELECT rowid FROM {table} WHERE {table} MATCH ? ORDER BY rank LIMIT ?", (match, pool * 4))]  # nosec B608
        if not cand:
            return []
        marks = ",".join("?" * len(cand))
        ok = {r[0] for r in self.db.execute(
            f"SELECT rowid FROM chunks WHERE rowid IN ({marks}) AND blocked = 0 AND access IN "  # nosec B608
            f"({','.join('?' * len(access))})", [*cand, *access])}
        hits = [r for r in cand if r in ok][:pool]
        if len(hits) >= min(pool, len(cand)) or len(cand) < pool * 4:
            return hits
        # Slow path (many restricted/quarantined candidates): filter inside the query.
        amarks = ",".join("?" * len(access))  # table is allowlisted; terms and access levels are bound
        sql = (f"SELECT c.rowid FROM {table} JOIN chunks c ON c.rowid = {table}.rowid "
               f"WHERE {table} MATCH ? AND c.blocked = 0 AND c.access IN ({amarks}) "
               f"ORDER BY bm25({table}) LIMIT ?")  # nosec B608
        return [r[0] for r in self.db.execute(sql, [match, *access, pool])]

    def _rank_bm25(self, query, access, pool):
        terms = self._selective("vocab_words", sorted(set(tokenize(query))), keep=12)
        return self._ranked("fts_words", terms, access, pool)

    def _rank_chargram(self, query, access, pool):
        grams = sorted({w[i:i + 3] for w in words(query) if len(w) >= 3 for i in range(len(w) - 2)})
        return self._ranked("fts_grams", self._selective("vocab_grams", grams, keep=16), access, pool)

    def _rank_dense(self, query, access, pool):
        if self.vectors is None:
            return []
        return self.vectors.search(self.embedder.embed([query])[0], pool, access)
