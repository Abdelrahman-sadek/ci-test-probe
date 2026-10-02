"""agentkit CLI: ingest · ask · chat · eval · test-agents · agents"""
import argparse
import json
import sys
from pathlib import Path

from .agents import load_all
from .chat import LibraryChat
from .evals import run_golden, run_smoke, to_markdown
from .llm import get_llm
from .rag import Index, ingest

DEFAULT_INDEX = "data/index.json"


def main(argv=None):
    p = argparse.ArgumentParser(prog="agentkit", description=__doc__)
    p.add_argument("--index", default=DEFAULT_INDEX)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest", help="OCR/extract + chunk + index files or folders")
    s.add_argument("paths", nargs="+")
    s.add_argument("--append", action="store_true")
    s = sub.add_parser("ask", help="ask one question")
    s.add_argument("question")
    s.add_argument("--debug", action="store_true", help="show route and retrieved chunks")
    sub.add_parser("chat", help="interactive chat")
    s = sub.add_parser("eval", help="run golden questions (AUC library)")
    s.add_argument("--out", default="data/eval-report.md")
    s = sub.add_parser("test-agents", help="smoke-test every agent (live needs ANTHROPIC_API_KEY)")
    s.add_argument("--out", default="data/agents-report.md")
    sub.add_parser("agents", help="list agents")
    a = p.parse_args(argv)
    llm = get_llm()
    mode = "LIVE (Anthropic)" if llm.live else "OFFLINE (fake LLM — set ANTHROPIC_API_KEY for real answers/OCR)"
    print(f"[{mode}]", file=sys.stderr)

    if a.cmd == "agents":
        for ag in load_all().values():
            print(f"{ag.division:12} {ag.name:32} {ag.description[:90]}")
    elif a.cmd == "ingest":
        base = Index.load(a.index) if a.append and Path(a.index).exists() else None
        index, report = ingest(a.paths, llm, base)
        index.save(a.index)
        for r in report:
            print(f"{'✗' if r['error'] else '✓'} {r['source']}: {r['chunks']} chunks {r['methods']} {r['error']}")
        print(f"Index: {len(index.chunks)} chunks → {a.index}")
    elif a.cmd == "test-agents":
        rep = run_smoke(llm)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(to_markdown("Agent smoke tests", rep), encoding="utf-8")
        print(f"{rep['passed']}/{rep['total']} agents passed → {a.out}" + (f"  missing tests: {rep['missing']}" if rep["missing"] else ""))
        return 0 if rep["passed"] == rep["total"] and not rep["missing"] else 1
    else:
        if not Path(a.index).exists():
            sys.exit(f"No index at {a.index}. Run: agentkit ingest samples/auc-library")
        chat = LibraryChat(Index.load(a.index), llm)
        if a.cmd == "ask":
            ans = chat.ask(a.question)
            if a.debug:
                hits = chat.index.search(a.question, 5, chat._expand(a.question, ans.lang))
                print(json.dumps({"agent": ans.agent, "lang": ans.lang, "guard": ans.guard,
                                  "hits": [(round(s, 2), c.title, c.section, c.method) for s, c in hits]},
                                 ensure_ascii=False, indent=1))
            print(ans.render())
        elif a.cmd == "chat":
            print("AUC Library assistant — type 'exit' to quit.")
            while (q := input("\n> ").strip()).lower() not in ("exit", "quit", ""):
                ans = chat.ask(q)
                print(f"\n({ans.agent})\n{ans.render()}")
        elif a.cmd == "eval":
            rep = run_golden(chat)
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(to_markdown("AUC Library golden eval", rep), encoding="utf-8")
            print(f"{rep['passed']}/{rep['total']} passed → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
