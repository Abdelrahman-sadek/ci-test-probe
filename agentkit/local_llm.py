"""Self-hosted models: a pool of OpenAI-compatible servers (vLLM, Ollama, llama.cpp, TGI, LM Studio), each with
roles. Select it with AGENTKIT_LLM=local.

Several models (config/local-models.json, or AGENTKIT_LOCAL_MODELS=<path>):
    {"models": [
      {"name": "main",   "url": "http://gpu1:8000/v1", "model": "Qwen/Qwen2.5-32B-Instruct-AWQ", "roles": ["answer"]},
      {"name": "arabic", "url": "http://gpu2:8000/v1", "model": "…", "roles": ["answer"], "langs": ["ar", "arabizi"]},
      {"name": "fast",   "url": "http://gpu2:8001/v1", "model": "Qwen/Qwen2.5-7B-Instruct", "roles": ["fast"]},
      {"name": "vision", "url": "http://gpu2:8002/v1", "model": "Qwen/Qwen2.5-VL-7B-Instruct", "roles": ["vision"]},
      {"name": "backup", "url": "http://cpu1:11434/v1", "model": "qwen2.5:7b", "roles": ["answer", "fast"], "priority": 9}
    ]}
Routing: a call needs a role (answer, fast, vision); candidates that list the question's language or the routed
agent come first, then by priority (lower first). If a server fails, the next candidate is tried; when all fail,
ResilientLLM answers from search results. One model only: AGENTKIT_LOCAL_URL / AGENTKIT_LOCAL_MODEL
(+ optional AGENTKIT_LOCAL_MODEL_FAST, AGENTKIT_LOCAL_VISION_MODEL).

Local models have no native citation API: answers are asked to cite [n] after each sentence; invalid numbers are
dropped and each cited source gets its best-matching sentence as the verbatim quote, so the evaluator-critic and
citation checks work as with Claude. Data never leaves the university network.
"""
import base64
import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from . import ROOT
from .arabic import detect_lang, tokenize
from .llm import LLM, NO_ANSWER, OCR_PROMPT, Grounded
from .metrics import METRICS, USAGE_CTX

log = logging.getLogger("agentkit.local")
CITE_RULES = ("\n\nSearch results are numbered [1], [2], … Answer only from them. End every sentence that uses a "
              "result with its number in brackets, e.g. [2]. If the results do not answer the question, reply exactly: "
              + NO_ANSWER)
ROLES = {"answer", "fast", "vision"}


@dataclass
class Endpoint:
    name: str
    url: str
    model: str
    roles: list[str] = field(default_factory=lambda: ["answer", "fast"])
    langs: list[str] = field(default_factory=list)    # preferred for these question languages
    agents: list[str] = field(default_factory=list)   # preferred for these agents
    priority: int = 5
    api_key_env: str = ""                             # name of the env var holding this server's key
    max_tokens: int = 800
    max_concurrency: int = 8                          # requests in flight per server; extra calls wait


def load_endpoints(path: str | Path | None = None) -> list[Endpoint]:
    path = Path(path or os.getenv("AGENTKIT_LOCAL_MODELS") or ROOT / "config/local-models.json")
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        eps = [Endpoint(**{k: v for k, v in m.items() if k in Endpoint.__dataclass_fields__}) for m in data["models"]]
        bad = [e.name for e in eps if not set(e.roles) <= ROLES]
        if bad:
            raise ValueError(f"unknown roles for {bad}; use {sorted(ROLES)}")
        return eps
    url = os.getenv("AGENTKIT_LOCAL_URL", "http://127.0.0.1:8000/v1")
    main = os.getenv("AGENTKIT_LOCAL_MODEL", "Qwen/Qwen2.5-14B-Instruct")
    eps = [Endpoint("main", url, main, ["answer"] if os.getenv("AGENTKIT_LOCAL_MODEL_FAST") else ["answer", "fast"],
                    api_key_env="AGENTKIT_LOCAL_API_KEY")]
    if os.getenv("AGENTKIT_LOCAL_MODEL_FAST"):
        eps.append(Endpoint("fast", url, os.environ["AGENTKIT_LOCAL_MODEL_FAST"], ["fast"], api_key_env="AGENTKIT_LOCAL_API_KEY"))
    if os.getenv("AGENTKIT_LOCAL_VISION_MODEL"):
        eps.append(Endpoint("vision", url, os.environ["AGENTKIT_LOCAL_VISION_MODEL"], ["vision"],
                            api_key_env="AGENTKIT_LOCAL_API_KEY"))
    return eps


class LocalLLM(LLM):
    live = True

    def __init__(self, endpoints: list[Endpoint] | None = None, post=None, timeout: float | None = None, **single):
        if single:  # LocalLLM(url=…, model=…) for one server
            endpoints = [Endpoint("main", single.get("url", "http://127.0.0.1:8000/v1"),
                                  single.get("model", "local"), ["answer", "fast"])]
        self.endpoints = endpoints if endpoints is not None else load_endpoints()
        self.timeout = timeout or float(os.getenv("AGENTKIT_LLM_TIMEOUT", "60"))
        self.post = post  # injectable for tests: post(url, payload, endpoint) -> dict
        import threading
        self._slots = {e.name: threading.BoundedSemaphore(max(1, e.max_concurrency)) for e in self.endpoints}

    def candidates(self, role: str, lang: str = "", agent: str = "") -> list[Endpoint]:
        fallback_role = {"fast": "answer"}.get(role)  # no fast model: the answer model does the checks
        eps = [e for e in self.endpoints if role in e.roles] or [e for e in self.endpoints if fallback_role in e.roles]
        return sorted(eps, key=lambda e: (agent not in e.agents, lang not in e.langs if e.langs else True, e.priority))

    def _chat(self, role: str, messages: list[dict], max_tokens: int, lang: str = "") -> str:
        agent = USAGE_CTX.get().get("agent", "")
        errors = []
        for ep in self.candidates(role, lang, agent):
            slot = self._slots[ep.name]
            if not slot.acquire(timeout=float(os.getenv("AGENTKIT_LOCAL_QUEUE_WAIT", "5"))):  # saturated: next server
                errors.append(f"{ep.name}: busy")
                continue
            try:
                data = self._post(ep, {"model": ep.model, "messages": messages, "max_tokens": max_tokens,
                                       "temperature": 0.1})
            except Exception as e:  # noqa: BLE001 — try the next server in the pool
                errors.append(f"{ep.name}: {type(e).__name__}")
                METRICS.inc("agentkit_local_failover_total", endpoint=ep.name)
                continue
            finally:
                slot.release()
            usage = data.get("usage") or {}
            METRICS.inc("agentkit_local_requests_total", endpoint=ep.name, role=role)
            METRICS.record_usage(ep.model, SimpleNamespace(input_tokens=usage.get("prompt_tokens", 0),
                                                           output_tokens=usage.get("completion_tokens", 0)))
            return (data["choices"][0]["message"].get("content") or "").strip()
        raise ConnectionError("no local model available for " + role + ": " + "; ".join(errors))

    def _post(self, ep: Endpoint, payload: dict) -> dict:
        url = ep.url.rstrip("/") + "/chat/completions"
        if self.post:
            return self.post(url, payload, ep)
        key = os.getenv(ep.api_key_env, "") if ep.api_key_env else ""
        req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json",
                                                                         **({"Authorization": f"Bearer {key}"} if key else {})})
        # the URL is operator configuration (servers on the campus network), not user input
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # nosec B310
            return json.loads(r.read())

    def complete(self, system, user, *, fast=False, max_tokens=None):
        return self._chat("fast" if fast else "answer", [{"role": "system", "content": system},
                                                          {"role": "user", "content": user}], max_tokens or 800,
                          detect_lang(user))

    def answer(self, system, question, sources):
        listing = "\n\n".join(f"[{i}] {s['title']}\n" + "\n".join(s["blocks"][1:] or s["blocks"])
                              for i, s in enumerate(sources, 1))
        text = self._chat("answer", [{"role": "system", "content": system + CITE_RULES},
                                     {"role": "user", "content": f"Search results:\n{listing}\n\nQuestion: {question}"}],
                          700, detect_lang(question))
        return ground(text, sources)

    def ocr_image(self, png, hint=""):
        if not self.candidates("vision"):
            from .ocr import _tesseract
            res = _tesseract(png)
            return f"{res[0]}\nCONFIDENCE: {'medium' if res[1] > 0.8 else 'low'}" if res else ""
        img = "data:image/png;base64," + base64.b64encode(png).decode()
        return self._chat("vision", [{"role": "user", "content": [
            {"type": "text", "text": OCR_PROMPT + (f"\nHint: {hint}" if hint else "")},
            {"type": "image_url", "image_url": {"url": img}}]}], 4000)

    def status(self) -> list[dict]:
        """Configured servers and their roles, for the dashboard System tab."""
        return [{"name": e.name, "model": e.model, "url": e.url, "roles": e.roles, "langs": e.langs,
                 "requests": sum(METRICS.value("agentkit_local_requests_total", endpoint=e.name, role=r) for r in e.roles),
                 "failovers": METRICS.value("agentkit_local_failover_total", endpoint=e.name)} for e in self.endpoints]


def ground(text: str, sources: list[dict]) -> Grounded:
    """Keep only citations that point at real sources and attach, per cited source, the sentence that best
    supports the answer (verbatim from the source), so quotes can be verified."""
    if not text or text.startswith(NO_ANSWER[:20]):
        return Grounded(NO_ANSWER)
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(sources)})
    text = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= len(sources) else "", text)
    words = set(tokenize(text))
    quotes = {}
    for n in cited:
        blocks = [b for b in sources[n - 1]["blocks"][1:] if b.strip() and not b.startswith("|")] or sources[n - 1]["blocks"]
        quotes[n] = [max(blocks, key=lambda b: len(words & set(tokenize(b))))]
    return Grounded(text.strip(), cited, quotes)
