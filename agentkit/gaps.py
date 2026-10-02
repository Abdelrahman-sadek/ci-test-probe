"""Knowledge-gap detection: group the questions the assistant could not answer well, so staff can write or fix
the page or notice that is missing.

Signals: handoffs because nothing matched (`unanswered`), 👎 feedback, and answers that cited an unchecked scan.
Grouping: stemmed terms plus glossary/Franco expansions, overlap similarity (≥ 2 shared concepts), single-link clusters (works offline,
Arabic, Franco and English versions of one need meet through the expansions). With AGENTKIT_DENSE=1 and an
embedder, cosine similarity of embeddings is used instead.
Privacy: questions are already redacted; a cluster is shown only with at least AGENTKIT_GAP_MIN_USERS (3)
distinct people (each anonymous question counts once).
"""
import hashlib
import os
import re
from collections import Counter

from .arabic import EN_STOP, normalize, tokenize, words

OWNERS = {"borrow": "Access services", "hours": "Access services", "rbscl": "RBSCL research services",
          "research-help": "Reference and instruction", "knowledge-fountain": "Scholarly communication",
          "src-library": "Social Research Center"}


def _signals(appdb, since: float) -> list[dict]:
    rows = []
    for i, ts, user, q, mode, lang in appdb.db.execute(
            "SELECT id, ts, user, question, mode, lang FROM unanswered WHERE ts >= ? AND mode = 'handoff'", (since,)):
        rows.append({"id": i, "ts": ts, "user": user, "question": appdb._dec(q), "lang": lang, "signal": "no match"})
    for i, ts, user, q, r, lang in appdb.db.execute(
            "SELECT id, ts, user, question, reason, lang FROM feedback WHERE ts >= ? AND rating < 0", (since,)):
        if q:
            rows.append({"id": i, "ts": ts, "user": user, "question": appdb._dec(q), "lang": lang,
                         "signal": "thumbs down", "reason": appdb._dec(r)})
    for i, ts, user, q, lang, tr in appdb.db.execute(
            "SELECT id, ts, user, question, lang, trace FROM answers WHERE ts >= ? AND trace LIKE '%unchecked_scan%'",
            (since,)):
        rows.append({"id": i, "ts": ts, "user": user, "question": appdb._dec(q), "lang": lang,
                     "signal": "unchecked scan"})
    return rows


def _terms(question: str, expand) -> set[str]:
    from .chat import GENERIC
    base = set(tokenize(question)) - EN_STOP - GENERIC
    if expand:
        base |= set(tokenize(" ".join(expand(question))))
    return {t for t in base if len(t) > 2}


def _cluster_sets(items: list[set], threshold: float) -> list[list[int]]:
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            shared = len(a & b)
            # overlap coefficient suits short questions; two shared concepts stop "book" alone from merging
            if a and b and shared >= min(2, len(a), len(b)) and shared / min(len(a), len(b)) >= threshold:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _cluster_vectors(vecs, threshold: float) -> list[list[int]]:
    import numpy as np
    v = np.asarray(vecs, dtype=float)
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    sims = v @ v.T
    return _cluster_sets([{j for j in range(len(v)) if sims[i, j] >= threshold} for i in range(len(v))], 0.01)


def detect(appdb, index=None, since: float = 0, expand=None, min_users: int | None = None,
           threshold: float | None = None) -> dict:
    min_users = min_users if min_users is not None else int(os.getenv("AGENTKIT_GAP_MIN_USERS", "3"))
    rows = _signals(appdb, since)
    if not rows:
        return {"clusters": [], "signals": 0, "hidden_small": 0}
    embedder = getattr(index, "embedder", None) if os.getenv("AGENTKIT_DENSE") == "1" else None
    terms = [_terms(r["question"], expand) for r in rows]
    if embedder is not None:
        groups = _cluster_vectors(embedder.embed([r["question"] for r in rows]), threshold or 0.80)
    else:
        groups = _cluster_sets(terms, threshold or 0.5)
    clusters, hidden = [], 0
    for g in groups:
        members = [rows[i] for i in g]
        people = len({m["user"] for m in members if m["user"]}) + sum(1 for m in members if not m["user"])
        if people < min_users:
            hidden += 1
            continue
        common = Counter(t for i in g for t in terms[i])
        top = [t for t, _ in common.most_common(6)]
        label_words = Counter(w for m in members for w in words(normalize(m["question"]))
                              if w not in EN_STOP and len(w) > 2 and not re.fullmatch(r"\d+", w))
        nearest = _nearest(index, top) if index is not None else None
        clusters.append({
            "id": "g" + hashlib.md5(" ".join(sorted(top)).encode(), usedforsecurity=False).hexdigest()[:10],
            "label": " · ".join(w for w, _ in label_words.most_common(4)),
            "terms": top, "size": len(members), "people": people,
            "signals": dict(Counter(m["signal"] for m in members)),
            "languages": dict(Counter(m["lang"] for m in members)),
            "examples": [m["question"] for m in members[:3]],
            "first_seen": min(m["ts"] for m in members), "last_seen": max(m["ts"] for m in members),
            "nearest": nearest,
            "kind": "missing page" if not nearest or nearest["coverage"] < 0.34 else "page exists but unclear",
            "owner": next((o for k, o in OWNERS.items() if nearest and k in nearest["origin"]), "Reference and instruction"),
        })
    clusters.sort(key=lambda c: (-c["people"], -c["last_seen"]))
    return {"clusters": clusters, "signals": len(rows), "hidden_small": hidden, "min_users": min_users}


def _nearest(index, terms: list[str]) -> dict | None:
    if not terms:
        return None
    hits = index.search(" ".join(terms), 1, [], ("public", "student", "faculty", "staff", "alumni"))
    if not hits:
        return None
    _, c = hits[0]
    coverage = len(set(terms) & set(c.tokens)) / len(terms)
    return {"title": c.title, "section": c.section, "origin": c.origin or c.source, "coverage": round(coverage, 2)}
