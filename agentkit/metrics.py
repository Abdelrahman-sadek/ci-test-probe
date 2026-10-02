"""Dependency-free metrics with Prometheus text exposition (GET /metrics).

Tracks request modes, per-stage latency (retrieve / generate / total), cache hits, LLM tokens and an
estimated USD cost per model, so you can watch no-answer rate, latency and spend on one dashboard.
"""
import threading
from collections import defaultdict

# USD per million tokens: (input, output, cache read). Cache writes bill at 1.25× input. Batch API: half price.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20),
          "claude-haiku-4-5": (1.0, 5.0, 0.10)}
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.counters: dict[tuple, float] = defaultdict(float)
        self.hist: dict[tuple, list] = {}

    def inc(self, name: str, value: float = 1.0, **labels):
        with self.lock:
            self.counters[(name, tuple(sorted(labels.items())))] += value

    def observe(self, name: str, seconds: float, **labels):
        key = (name, tuple(sorted(labels.items())))
        with self.lock:
            h = self.hist.setdefault(key, [[0] * len(BUCKETS), 0.0, 0])
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    h[0][i] += 1
            h[1] += seconds
            h[2] += 1

    def record_usage(self, model: str, usage, batch: bool = False):
        """Count tokens and estimated cost from an Anthropic `usage` object."""
        if usage is None:
            return
        get = (lambda k: getattr(usage, k, 0) or 0)
        inp, out = get("input_tokens"), get("output_tokens")
        cr, cw = get("cache_read_input_tokens"), get("cache_creation_input_tokens")
        for kind, n in (("input", inp), ("output", out), ("cache_read", cr), ("cache_write", cw)):
            if n:
                self.inc("agentkit_llm_tokens_total", n, model=model, type=kind)
        pi, po, pc = PRICES.get(model, (0.0, 0.0, 0.0))
        cost = (inp * pi + out * po + cr * pc + cw * pi * 1.25) / 1e6 * (0.5 if batch else 1.0)
        if cost:
            self.inc("agentkit_llm_cost_usd_total", cost, model=model)

    def value(self, name: str, **labels) -> float:
        with self.lock:
            if labels:
                return self.counters.get((name, tuple(sorted(labels.items()))), 0.0)
            return sum(v for (n, _), v in self.counters.items() if n == name)

    def render(self) -> str:
        def fmt(labels, extra=()):
            items = list(labels) + list(extra)
            return "{" + ",".join(f'{k}="{v}"' for k, v in items) + "}" if items else ""

        out = []
        with self.lock:
            for (name, labels), v in sorted(self.counters.items()):
                out.append(f"{name}{fmt(labels)} {v:g}")
            for (name, labels), (counts, total, n) in sorted(self.hist.items()):
                for b, c in zip(BUCKETS, counts):
                    out.append(f"{name}_bucket{fmt(labels, [('le', b)])} {c}")
                out.append(f'{name}_bucket{fmt(labels, [("le", "+Inf")])} {n}')
                out += [f"{name}_sum{fmt(labels)} {total:g}", f"{name}_count{fmt(labels)} {n}"]
        return "\n".join(out) + "\n"


METRICS = Metrics()
