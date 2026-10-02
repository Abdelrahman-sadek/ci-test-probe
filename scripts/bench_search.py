"""Search latency benchmark on a synthetic corpus (seed pages × variations) for both back ends.

    python scripts/bench_search.py --chunks 20000 --target-p95-ms 300
"""
import argparse
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentkit.llm import FakeLLM  # noqa: E402
from agentkit.rag import Chunk, Index, ingest  # noqa: E402
from agentkit.store_sqlite import SqliteIndex  # noqa: E402

TOPICS = ["borrowing", "renewal", "fines", "theses", "manuscripts", "printing", "study rooms", "databases",
          "Egyptology", "photographs", "alumni", "visitors", "reading room", "interlibrary loan", "catalog"]
QUERIES = ["Can alumni borrow books?", "how do I renew", "rare manuscripts reading room", "ممكن الخريجين يستعيروا كتب",
           "master's theses repository", "printing cost per page", "Egyptology photographs collection"]


def synthetic(n: int, seed_chunks: list[Chunk]) -> list[Chunk]:
    rnd, out = random.Random(7), []
    for i in range(n):
        base = seed_chunks[i % len(seed_chunks)]
        extra = " ".join(rnd.sample(TOPICS, 4))
        out.append(Chunk(f"s{i}", f"{base.text}\nRelated: {extra} #{i}", base.title, base.section, f"{base.source}#{i}",
                         1, base.lang, "text", origin=f"synthetic-{i % 500}"))
    return out


def bench(index, label: str, target_ms: float) -> bool:
    times = []
    for _ in range(5):
        for q in QUERIES:
            t0 = time.perf_counter()
            index.search(q, 5)
            times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    p95 = times[int(0.95 * len(times)) - 1]
    ok = not target_ms or p95 <= target_ms
    print(f"{label:10} chunks={index.size:>7}  p50={times[len(times) // 2]:7.1f} ms  p95={p95:7.1f} ms  {'✓' if ok else '✗'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", type=int, default=20000)
    ap.add_argument("--target-p95-ms", type=float, default=0)
    ap.add_argument("--backends", default="json,sqlite")
    a = ap.parse_args()
    seed, _ = ingest([Path(__file__).resolve().parent.parent / "knowledge/auc-library/pages"], FakeLLM(), safe=False)
    chunks = synthetic(a.chunks, seed.chunks)
    ok = True
    if "json" in a.backends:
        t0 = time.perf_counter()
        idx = Index(chunks)
        print(f"json build {time.perf_counter() - t0:.1f}s")
        ok &= bench(idx, "json", a.target_p95_ms)
    if "sqlite" in a.backends:
        with tempfile.TemporaryDirectory() as d:
            t0 = time.perf_counter()
            idx = SqliteIndex(Path(d) / "b.db")
            for i in range(0, len(chunks), 2000):
                idx.add(chunks[i:i + 2000])
            print(f"sqlite build {time.perf_counter() - t0:.1f}s")
            ok &= bench(idx, "sqlite", a.target_p95_ms)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
