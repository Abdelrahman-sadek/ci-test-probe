"""LLM layer: Anthropic when credentials exist, a deterministic extractive stand-in otherwise (offline tests).

Live defaults: main model `claude-opus-5-5` at low effort (chat/RAG answers rarely need more), fast model
`claude-haiku-4-5` for query rewriting, contextualising chunks and grading. Grounded answers use native
`search_result` blocks so citations come from the API, not from parsing "[n]" out of text.
"""
import base64
import math
import os
from collections import Counter
from dataclasses import dataclass, field

from .arabic import tokenize
from .metrics import METRICS

MODEL_SMART = os.getenv("AGENTKIT_MODEL", "claude-opus-5-5")
MODEL_FAST = os.getenv("AGENTKIT_MODEL_FAST", "claude-haiku-4-5")
EFFORT = os.getenv("AGENTKIT_EFFORT", "low")
_NO_EFFORT = ("claude-haiku", "claude-sonnet-4-5", "claude-3")  # these reject output_config.effort
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
REFUSAL_TEXT = "I can't help with that request. Please contact an AUC librarian for assistance."
NO_ANSWER = "I don't know based on the library sources I have."
CONTEXT_PROMPT = ("Here is the chunk we want to situate within the whole document\n<chunk>\n{chunk}\n</chunk>\n"
                  "Please give a short succinct context to situate this chunk within the overall document for the "
                  "purposes of improving search retrieval of the chunk. Answer only with the succinct context and "
                  "nothing else.")
FIGURE_PROMPT = ("For each figure, chart, diagram or equation on this page write one line: "
                 "'[figure: <caption if any> — <one-sentence description of what it shows>]' or "
                 "'[formula: <LaTeX>]'. Output only those lines; output nothing if there are none.")
OCR_PROMPT = ("Transcribe ALL text in this scanned page exactly, in reading order. Keep Arabic in Arabic script and "
              "English in English; preserve headings, lists and tables (tables as Markdown). Write unreadable spans "
              "as [illegible]. Transcribe handwritten notes and margin annotations where they appear as "
              "[handwritten: …]. Write mathematical formulas as LaTeX between $…$ and describe figures/diagrams as "
              "[figure: caption — what it shows]. Output only the transcription, then a final line 'CONFIDENCE: high', "
              "'CONFIDENCE: medium' or 'CONFIDENCE: low' for how legible the page was.")


@dataclass
class Grounded:
    """An answer plus the 1-based source numbers it cites and the exact quoted text per source."""
    text: str
    cited: list[int] = field(default_factory=list)
    quotes: dict[int, list[str]] = field(default_factory=dict)
    degraded: str = ""  # "outage" | "budget" when the answer came from the extractive fallback


class LLM:
    live = False

    def complete(self, system: str, user: str, *, fast: bool = False, max_tokens: int | None = None) -> str:
        raise NotImplementedError

    def answer(self, system: str, question: str, sources: list[dict]) -> Grounded:
        """sources: [{"title", "source", "blocks": [str, ...]}] — blocks are the citable units."""
        raise NotImplementedError

    def ocr_image(self, png: bytes, hint: str = "") -> str:
        raise NotImplementedError

    def contextualize(self, document: str, chunk: str) -> str:
        return ""

    def describe_figures(self, png: bytes) -> str:
        return ""

    def contextualize_batch(self, items: list[tuple[str, str]]) -> list[str]:
        return [self.contextualize(doc, chunk) for doc, chunk in items]

    def stream_answer(self, system: str, question: str, sources: list[dict]):
        """Yield ("delta", text) events, then ("done", Grounded). Default: one delta with the full answer."""
        g = self.answer(system, question, sources)
        yield "delta", g.text
        yield "done", g


class AnthropicLLM(LLM):
    live = True

    def __init__(self, client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic(timeout=float(os.getenv("AGENTKIT_LLM_TIMEOUT", "30")), max_retries=2)
        self.client = client

    @staticmethod
    def _kwargs(model: str, max_tokens: int, content, system: str = "") -> dict:
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}
        if system:  # stable text first + cache_control → prompt caching on repeat calls
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if not model.startswith(_NO_EFFORT):
            kwargs["output_config"] = {"effort": EFFORT}
        if model in _FALLBACK_MODELS:  # a policy decline is retried server-side on a fallback model
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        return kwargs

    def _create(self, model: str, max_tokens: int, content, system: str = ""):
        kwargs = self._kwargs(model, max_tokens, content, system)
        api = self.client.beta.messages if "betas" in kwargs else self.client.messages
        resp = api.create(**kwargs)
        METRICS.record_usage(model, getattr(resp, "usage", None))
        return resp

    @staticmethod
    def _text(resp) -> str:
        if resp.stop_reason == "refusal":
            return REFUSAL_TEXT
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def complete(self, system, user, *, fast=False, max_tokens=None):
        model = MODEL_FAST if fast else MODEL_SMART
        return self._text(self._create(model, max_tokens or (1024 if fast else 4096), user, system))

    @staticmethod
    def _blocks(sources, question):
        return [{"type": "search_result", "source": s["source"], "title": s["title"],
                 "content": [{"type": "text", "text": t} for t in s["blocks"]],
                 "citations": {"enabled": True}} for s in sources] + [{"type": "text", "text": question}]

    @staticmethod
    def _grounded(resp) -> Grounded:
        if resp.stop_reason == "refusal":
            return Grounded(REFUSAL_TEXT)
        parts, quotes = [], {}
        for b in resp.content:
            if b.type != "text":
                continue
            parts.append(b.text)
            nums = []
            for c in getattr(b, "citations", None) or []:
                if getattr(c, "type", "") == "search_result_location":
                    n = c.search_result_index + 1
                    quotes.setdefault(n, []).append(c.cited_text)
                    nums.append(n)
            parts += [f" [{n}]" for n in sorted(set(nums))]
        return Grounded("".join(parts).strip(), sorted(quotes), quotes)

    def answer(self, system, question, sources):
        return self._grounded(self._create(MODEL_SMART, 4096, self._blocks(sources, question), system))

    def stream_answer(self, system, question, sources):
        """Stream text deltas as they arrive; the final message carries the citations."""
        kwargs = self._kwargs(MODEL_SMART, 4096, self._blocks(sources, question), system)
        api = self.client.beta.messages if "betas" in kwargs else self.client.messages
        with api.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield "delta", text
            final = stream.get_final_message()
        METRICS.record_usage(MODEL_SMART, getattr(final, "usage", None))
        yield "done", self._grounded(final)

    def contextualize_batch(self, items, poll_seconds: float = 30.0):
        """Contextual Retrieval through the Message Batches API (asynchronous, ~50% cheaper)."""
        import time
        requests = []
        for i, (doc, chunk) in enumerate(items):
            params = self._kwargs(MODEL_FAST, 300, CONTEXT_PROMPT.format(chunk=chunk), f"<document>\n{doc}\n</document>")
            params.pop("betas", None)
            params.pop("fallbacks", None)
            requests.append({"custom_id": f"c{i}", "params": params})
        if not requests:
            return []
        batch = self.client.messages.batches.create(requests=requests)
        while self.client.messages.batches.retrieve(batch.id).processing_status != "ended":
            time.sleep(poll_seconds)
        out = {}
        for r in self.client.messages.batches.results(batch.id):  # results arrive in any order
            if r.result.type == "succeeded":
                out[r.custom_id] = "".join(b.text for b in r.result.message.content if b.type == "text").strip()
                METRICS.record_usage(MODEL_FAST, getattr(r.result.message, "usage", None), batch=True)
        return [out.get(f"c{i}", "") for i in range(len(items))]

    def ocr_image(self, png, hint=""):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": base64.b64encode(png).decode()}},
                   {"type": "text", "text": f"{OCR_PROMPT} {hint}".strip()}]
        return self._text(self._create(MODEL_SMART, 16000, content))

    def describe_figures(self, png):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": base64.b64encode(png).decode()}},
                   {"type": "text", "text": FIGURE_PROMPT}]
        return self._text(self._create(MODEL_SMART, 2000, content))

    def contextualize(self, document, chunk):
        system = f"<document>\n{document}\n</document>"  # cached once per document, reused for every chunk
        return self._text(self._create(MODEL_FAST, 300, CONTEXT_PROMPT.format(chunk=chunk), system))


class FakeLLM(LLM):
    """Offline, deterministic stand-in. Answers extractively — the cited sentence with the most query-term
    overlap — so routing, retrieval, citations and evals are all testable without an API key."""

    def __init__(self, ocr_text: str = "[fake OCR text]"):
        self.ocr_text = ocr_text
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, *, fast=False, max_tokens=None):
        self.calls.append((system, user))
        if "No library documents matched" in system:
            return "Search strategy: combine your key concepts with AND, synonyms with OR, in English and Arabic."
        return NO_ANSWER

    def answer(self, system, question, sources):
        self.calls.append((system, question))
        q = set(tokenize(question))
        cands = [(i, sent, set(tokenize(sent))) for i, src in enumerate(sources, 1) for sent in src["blocks"][1:]]
        pri = [c for c in cands if sources[c[0] - 1].get("priority") and q & c[2]]
        if pri:  # mirror the live rule: notices and live data win over stored pages
            cands = pri
        df = Counter(t for _, _, toks in cands for t in toks)  # rare terms ("outside", "subject") weigh more
        scored = sorted(((sum(math.log(1 + len(cands) / df[t]) for t in q & toks) / math.sqrt(i), i, sent, toks)
                         for i, sent, toks in cands), key=lambda x: -x[0])  # rank prior
        best = scored[0][:3] if scored and scored[0][0] else None
        if best is None:  # no lexical overlap (e.g. Arabic question, English page): trust the top-ranked source
            lead = next((b for b in sources[0]["blocks"][1:] if b.strip() and not b.startswith("|")), "") \
                if sources else ""
            if not lead:
                return Grounded(NO_ANSWER)
            best = (0, 1, lead)
        top, n, sent = best
        # A second sentence from the same source joins when it is nearly as relevant and covers other query terms.
        used = next((t for s_, i, x, t in scored if x == sent), set()) & q
        extra = next((x for s_, i, x, t in scored[1:4] if i == n and s_ >= 0.6 * top and (t & q) - used), "")
        quotes = [sent] + ([extra] if extra else [])
        return Grounded(f"{sources[n - 1]['title']}: {' '.join(quotes)} [{n}]", [n], {n: quotes})

    def stream_answer(self, system, question, sources):
        g = self.answer(system, question, sources)
        for word in g.text.split(" "):
            yield "delta", word + " "
        yield "done", g

    def ocr_image(self, png, hint=""):
        return self.ocr_text


class ResilientLLM(LLM):
    """Production wrapper around the live model:
    * circuit breaker — after 3 consecutive failures the model is skipped for AGENTKIT_LLM_COOLDOWN seconds;
    * daily budget — above AGENTKIT_DAILY_BUDGET_USD of estimated spend, answers switch to extractive mode;
    * graceful degradation — failures fall back to the extractive answerer, marked `degraded` so the UI can
      show a "search results only" banner instead of an error."""

    def __init__(self, inner: LLM, fallback: LLM | None = None, budget_usd: float | None = None,
                 cooldown: float | None = None):
        self.inner, self.fallback = inner, fallback or FakeLLM()
        env_budget = os.getenv("AGENTKIT_DAILY_BUDGET_USD")
        self.budget = budget_usd if budget_usd is not None else (float(env_budget) if env_budget else None)
        self.cooldown = cooldown if cooldown is not None else float(os.getenv("AGENTKIT_LLM_COOLDOWN", "60"))
        self.failures, self.open_until = 0, 0.0
        self._day, self._day_start_cost = "", 0.0

    def _cost_today(self) -> float:
        import datetime
        today = datetime.date.today().isoformat()
        total = METRICS.value("agentkit_llm_cost_usd_total")
        if today != self._day:
            self._day, self._day_start_cost = today, total
        return total - self._day_start_cost

    monitor = None  # BudgetMonitor: persistent spend and alerts (wired by make_chat)

    @property
    def brake(self) -> bool:
        """Near the budget: skip optional calls (grading, rewriting, critic) before answers degrade."""
        return bool(self.monitor and self.monitor.brake())

    def status(self) -> str:
        import time
        if self.monitor is not None and self.monitor.over():
            return "budget"
        if self.monitor is None and self.budget is not None and self._cost_today() >= self.budget:
            return "budget"
        if time.monotonic() < self.open_until:
            return "outage"
        return "ok"

    @property
    def live(self) -> bool:
        return self.inner.live and self.status() == "ok"

    def _call(self, name: str, *args, **kwargs):
        import time
        state = self.status()
        if state == "ok":
            try:
                out = getattr(self.inner, name)(*args, **kwargs)
                self.failures = 0
                return out, ""
            except Exception:  # noqa: BLE001 — any API/network failure degrades instead of erroring
                self.failures += 1
                METRICS.inc("agentkit_llm_failures_total")
                if self.failures >= 3:
                    self.open_until = time.monotonic() + self.cooldown
                state = "outage"
        METRICS.inc("agentkit_degraded_total", reason=state)
        return getattr(self.fallback, name)(*args, **kwargs), state

    def complete(self, system, user, *, fast=False, max_tokens=None):
        return self._call("complete", system, user, fast=fast, max_tokens=max_tokens)[0]

    def answer(self, system, question, sources):
        g, state = self._call("answer", system, question, sources)
        g.degraded = state
        return g

    def stream_answer(self, system, question, sources):
        if self.status() != "ok":
            g = self.answer(system, question, sources)
            yield "delta", g.text
            yield "done", g
            return
        try:
            yield from self.inner.stream_answer(system, question, sources)
            self.failures = 0
        except Exception:  # noqa: BLE001
            self.failures += 1
            METRICS.inc("agentkit_llm_failures_total")
            g = self.fallback.answer(system, question, sources)
            g.degraded = "outage"
            yield "delta", g.text
            yield "done", g

    def ocr_image(self, png, hint=""):
        from .ocr import _tesseract
        try:
            if self.status() == "ok":
                return self.inner.ocr_image(png, hint)
        except Exception:  # noqa: BLE001
            METRICS.inc("agentkit_llm_failures_total")
        res = _tesseract(png)  # local OCR keeps ingestion going during an outage or over budget
        return f"{res[0]}\nCONFIDENCE: {'medium' if res[1] > 0.8 else 'low'}" if res else ""

    def contextualize(self, document, chunk):
        return self._call("contextualize", document, chunk)[0] if self.status() == "ok" else ""

    def describe_figures(self, png):
        return self._call("describe_figures", png)[0] if self.status() == "ok" else ""

    def contextualize_batch(self, items):
        return self.inner.contextualize_batch(items) if self.status() == "ok" else ["" for _ in items]


def get_llm() -> LLM:
    has_creds = any(os.getenv(v) for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")) or \
        os.getenv("AGENTKIT_LIVE") == "1"  # e.g. credentials from an `ant auth login` profile
    if has_creds and os.getenv("AGENTKIT_FAKE") != "1":
        return ResilientLLM(AnthropicLLM())
    return FakeLLM()
