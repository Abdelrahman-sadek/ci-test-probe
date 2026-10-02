"""Load test the HTTP API: concurrent POST /api/ask for a fixed time; prints throughput and latency.

    python scripts/loadtest.py http://127.0.0.1:8000 --concurrency 20 --seconds 15 --target-rps 50 --target-p95 3
Exit code 1 if a target is missed. Uses only the standard library.
"""
import argparse
import json
import statistics
import sys
import threading
import time
import urllib.request

QUESTIONS = ["Can alumni borrow books?", "How do I renew a book?", "Where is the entrance to the rare books library?",
             "ممكن الخريجين يستعيروا كتب؟", "Where can I find AUC master's theses?", "Is the SRC library open on Friday?"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--target-rps", type=float, default=0)
    ap.add_argument("--target-p95", type=float, default=0, help="seconds")
    a = ap.parse_args()
    lat, errors, lock, stop = [], [0], threading.Lock(), time.time() + a.seconds

    def worker(n):
        i = n
        while time.time() < stop:
            body = json.dumps({"question": QUESTIONS[i % len(QUESTIONS)]}).encode()
            req = urllib.request.Request(a.url.rstrip("/") + "/api/ask", data=body,
                                         headers={"Content-Type": "application/json"})
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310 — operator-supplied test URL
                    r.read()
                with lock:
                    lat.append(time.perf_counter() - t0)
            except Exception:  # noqa: BLE001
                with lock:
                    errors[0] += 1
            i += 1

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(a.concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if not lat:
        sys.exit("no successful requests")
    lat.sort()
    p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))]  # noqa: E731
    rps = len(lat) / a.seconds
    print(json.dumps({"requests": len(lat), "errors": errors[0], "rps": round(rps, 1), "p50_s": round(p(.5), 3),
                      "p95_s": round(p(.95), 3), "p99_s": round(p(.99), 3), "mean_s": round(statistics.mean(lat), 3)}))
    missed = (a.target_rps and rps < a.target_rps) or (a.target_p95 and p(.95) > a.target_p95) or errors[0]
    sys.exit(1 if missed else 0)


if __name__ == "__main__":
    main()
