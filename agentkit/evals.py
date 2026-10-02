"""Golden-set evaluation (routing, answer mode, retrieval recall@k / MRR, citations, key facts) and agent smoke tests."""
import json
import re
from pathlib import Path

from . import ROOT
from . import style
from .agents import load_all
from .arabic import normalize
from .chat import LibraryChat
from .llm import LLM

SHORT = {"concierge": "auc-library-concierge", "research-assistant": "auc-research-assistant",
         "catalog-navigator": "auc-catalog-navigator", "special-collections": "auc-special-collections-guide"}
GOLDEN = ROOT / "evals/auc-library/golden-questions.md"
_CELL = re.compile(r"(?<!\\)\|")


def load_golden(path: Path = GOLDEN) -> list[dict]:
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("|")]

    def cells(line):
        return [c.strip().replace("\\|", "|") for c in _CELL.split(line.strip().strip("|"))]

    header = cells(rows[0])
    return [dict(zip(header, cells(r))) for r in rows[2:]]


def _matches(chunk, gold: list[str]) -> bool:
    hay = f"{chunk.title} {chunk.source}".lower()
    return any(g in hay for g in gold)


def run_golden(chat: LibraryChat, path: Path = GOLDEN) -> dict:
    results, rr, recalled, with_gold = [], [], 0, 0
    for row in load_golden(path):
        ans = chat.ask(row["question"])
        want = SHORT.get(row["agent"])
        checks = {"mode": ans.mode == row["expect"],
                  "route": want is None or ans.agent == want,
                  "lang": row["lang"] == "en" or ans.lang == row["lang"]}
        gold = [g.strip().lower() for g in row.get("gold", "").split("|") if g.strip()]
        rank = 0
        if gold:
            with_gold += 1
            rank = next((r for r, (_, c) in enumerate(ans.hits, 1) if _matches(c, gold)), 0)
            recalled += bool(rank)
            rr.append(1 / rank if rank else 0.0)
            checks["retrieval"] = bool(rank)
            checks["citation"] = any(_matches(c, gold) for _, c in ans.sources)
        facts = [f.strip() for f in row.get("facts", "").split(";") if f.strip()]
        if facts:
            checks["facts"] = all(normalize(f) in normalize(ans.text) for f in facts)
        if ans.mode in ("answer", "strategy"):
            checks["style"] = not style.findings(ans.text)
        if ans.quotes:  # citation precision: every quote must appear verbatim in the chunk it cites
            cited = dict(ans.sources)
            checks["quoted"] = all(n in cited and normalize(q) in normalize(cited[n].text)
                                   for n, qs in ans.quotes.items() for q in qs)
        if chat.llm.live and row["expect"] == "answer" and ans.quotes:
            quotes = "\n".join(q for qs in ans.quotes.values() for q in qs)
            verdict = chat.llm.complete("You check a library chatbot for faithfulness. Reply PASS if every claim in "
                                        "the answer is supported by the quotes, else FAIL.",
                                        f"Quotes:\n{quotes}\n\nAnswer:\n{ans.text}", fast=True, max_tokens=10)
            checks["faithful"] = verdict.upper().startswith("PASS")
        results.append({"id": row["id"], "lang": row["lang"], "question": row["question"], "expect": row["expect"],
                        "mode": ans.mode, "agent": ans.agent, "rank": rank, "checks": checks,
                        "pass": all(checks.values()), "answer": ans.render()})
    n = len(results)
    by_lang = {}
    for r in results:
        b = by_lang.setdefault(r["lang"], [0, 0])
        b[0] += r["pass"]
        b[1] += 1
    return {"passed": sum(r["pass"] for r in results), "total": n,
            "pass_rate": round(sum(r["pass"] for r in results) / max(n, 1), 3),
            "recall_at_k": round(recalled / max(with_gold, 1), 3), "mrr": round(sum(rr) / max(len(rr), 1), 3),
            "k": chat.k, "by_lang": {k: f"{p}/{t}" for k, (p, t) in by_lang.items()},
            "live": bool(chat.llm.live), "index_version": chat.index.version, "config": config_fingerprint(),
            "results": results}


def run_smoke(llm: LLM, path: Path = ROOT / "evals/agents/smoke.json") -> dict:
    """Send each agent its test prompt; a cheap judge checks the reply against the expectation."""
    agents, tests = load_all(), json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(set(agents) - set(tests))
    results = []
    for name, t in tests.items():
        if not llm.live:
            results.append({"agent": name, "pass": name in agents, "note": "offline: structure only"})
            continue
        reply = llm.complete(agents[name].body, t["prompt"], max_tokens=4096)
        verdict = llm.complete("You are a strict QA judge. Reply 'PASS' or 'FAIL: <reason>' only.",
                               f"Expectation: {t['expect']}\n\nAgent reply:\n{reply}", fast=True, max_tokens=60)
        results.append({"agent": name, "pass": verdict.upper().startswith("PASS"), "note": verdict, "reply": reply})
    return {"passed": sum(r["pass"] for r in results), "total": len(results), "missing": missing, "results": results}


def to_markdown(title: str, report: dict) -> str:
    lines = [f"# {title}", "", f"**{report['passed']}/{report['total']} passed**"]
    if "recall_at_k" in report:
        lines += [f"recall@{report['k']}: **{report['recall_at_k']}** · MRR: **{report['mrr']}** · "
                  f"by language: {report['by_lang']}"]
    lines.append("")
    for r in report["results"]:
        mark = "✅" if r["pass"] else "❌"
        if "question" in r:
            failed = [k for k, v in r["checks"].items() if not v]
            lines.append(f"- {mark} `{r['id']}` {r['question']} → {r['agent']} ({r['mode']})"
                         + (f" — failed: {', '.join(failed)}" if failed else ""))
        else:
            lines.append(f"- {mark} {r['agent']} {r.get('note', '')}")
    return "\n".join(lines) + "\n"


def config_fingerprint() -> str:
    """Hash of everything besides the index that changes answers: models, agent prompts, guards, routes, style
    rules. Preflight requires evals to have run on the same fingerprint."""
    import hashlib
    from . import chat as chat_mod, llm as llm_mod
    parts = [llm_mod.MODEL_SMART, llm_mod.MODEL_FAST, style.RULES, repr(chat_mod.GUARDS), repr(chat_mod.ROUTES),
             *(f"{n}:{a.body}" for n, a in sorted(load_all().items()))]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]
