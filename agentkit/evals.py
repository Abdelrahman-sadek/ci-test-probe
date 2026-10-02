"""Run the golden question set and the per-agent smoke tests."""
import json
import re
from pathlib import Path

from . import ROOT
from .agents import load_all
from .chat import LibraryChat
from .llm import LLM

SHORT = {"concierge": "auc-library-concierge", "research-assistant": "auc-research-assistant",
         "catalog-navigator": "auc-catalog-navigator", "special-collections": "auc-special-collections-guide"}


def load_golden(path: Path) -> list[dict]:
    rows = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("|")]
    header = [h.strip() for h in rows[0].strip("|").split("|")]
    return [dict(zip(header, (c.strip() for c in r.strip("|").split("|")))) for r in rows[2:]]


def run_golden(chat: LibraryChat, path: Path = ROOT / "evals/auc-library/golden-questions.md") -> dict:
    results = []
    for row in load_golden(path):
        ans = chat.ask(row["question"])
        want_agent = SHORT.get(row["agent"])
        refused = not ans.found
        checks = {
            "route": want_agent is None or ans.agent == want_agent or bool(ans.guard),
            "refusal": refused == (row["refuse"] == "yes"),
            "lang": row["lang"] == "en" or ans.lang == row["lang"],
        }
        if row.get("expected") and row["refuse"] != "yes" and chat.llm.live:
            verdict = chat.llm.complete("You grade a library chatbot. Reply PASS or FAIL only.",
                                        f"Expected: {row['expected']}\nGot: {ans.text}", fast=True, max_tokens=5)
            checks["answer"] = verdict.upper().startswith("PASS")
        results.append({"id": row["id"], "question": row["question"], "agent": ans.agent, "guard": ans.guard,
                        "answer": ans.render(), "checks": checks, "pass": all(checks.values())})
    return {"passed": sum(r["pass"] for r in results), "total": len(results), "results": results}


def run_smoke(llm: LLM, path: Path = ROOT / "evals/agents/smoke.json") -> dict:
    """Send each agent its test prompt; a cheap judge checks the reply against the expectation."""
    agents, tests = load_all(), json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(set(agents) - set(tests))
    results = []
    for name, t in tests.items():
        if not llm.live:
            results.append({"agent": name, "pass": name in agents, "note": "offline: structure only"})
            continue
        reply = llm.complete(agents[name].body, t["prompt"], max_tokens=1500)
        verdict = llm.complete("You are a strict QA judge. Reply 'PASS' or 'FAIL: <reason>' only.",
                               f"Expectation: {t['expect']}\n\nAgent reply:\n{reply}", fast=True, max_tokens=60)
        results.append({"agent": name, "pass": verdict.upper().startswith("PASS"), "note": verdict,
                        "reply": reply})
    return {"passed": sum(r["pass"] for r in results), "total": len(results), "missing": missing, "results": results}


def to_markdown(title: str, report: dict) -> str:
    lines = [f"# {title}", "", f"**{report['passed']}/{report['total']} passed**", ""]
    for r in report["results"]:
        mark = "✅" if r["pass"] else "❌"
        label = r.get("question") or r.get("agent")
        detail = r.get("checks") or r.get("note", "")
        lines.append(f"- {mark} `{r.get('id', '')}` {label} → {r.get('agent', '')} {detail}")
    return "\n".join(lines) + "\n"
