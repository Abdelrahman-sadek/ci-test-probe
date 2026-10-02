"""agentkit CLI: agents · ingest · ask · chat · eval · test-agents · serve · mcp"""
import argparse
import json
import os
import sys
from pathlib import Path

from .agents import load_all
from .chat import LibraryChat
from .connectors import PrimoCatalog
from .evals import run_golden, run_smoke, to_markdown
from .llm import get_llm
from .rag import Embedder, Index, Reranker, ingest

DEFAULT_INDEX = os.getenv("AGENTKIT_INDEX", "data/index.json")
SEED_CORPUS = "knowledge/auc-library/pages"


class SetupError(RuntimeError):
    """Missing index or optional dependency; raised (not sys.exit) so the MCP server and web UI survive it."""


def _optional(flag: str, factory):
    if os.getenv(flag) != "1":
        return None
    try:
        return factory()
    except ImportError as e:
        raise SetupError(f"{flag}=1 needs the optional extras: pip install -e '.[dense]'") from e


def load_index(path: str = DEFAULT_INDEX) -> Index:
    if not Path(path).exists():
        raise SetupError(f"No index at {path}. Run: agentkit ingest {SEED_CORPUS}")
    return Index.load(path, embedder=_optional("AGENTKIT_DENSE", Embedder),
                      reranker=_optional("AGENTKIT_RERANK", Reranker))


def make_chat(index_path: str = DEFAULT_INDEX, llm=None) -> LibraryChat:
    return LibraryChat(load_index(index_path), llm or get_llm(), catalog=PrimoCatalog.from_env(),
                       log_path=os.getenv("AGENTKIT_LOG") or None)


def main(argv=None):
    try:
        return _main(argv)
    except SetupError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1


def _main(argv=None):
    p = argparse.ArgumentParser(prog="agentkit", description=__doc__)
    p.add_argument("--index", default=DEFAULT_INDEX)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("agents", help="list agents")
    s = sub.add_parser("ingest", help="OCR/extract + chunk + index files or folders")
    s.add_argument("paths", nargs="+")
    s.add_argument("--append", action="store_true")
    s.add_argument("--contextualize", action="store_true", help="LLM context per chunk (live mode)")
    s = sub.add_parser("ask", help="ask one question")
    s.add_argument("question")
    s.add_argument("--debug", action="store_true", help="show route, mode and retrieved chunks")
    sub.add_parser("chat", help="interactive chat (keeps follow-up context)")
    s = sub.add_parser("eval", help="golden questions: routing, retrieval, citations, facts")
    s.add_argument("--out", default="data/eval-report.md")
    s.add_argument("--json", default="data/eval-report.json")
    s.add_argument("--min-pass", type=float, default=0.0)
    s.add_argument("--min-recall", type=float, default=0.0)
    s = sub.add_parser("test-agents", help="smoke-test every agent (live mode grades replies)")
    s.add_argument("--out", default="data/agents-report.md")
    s = sub.add_parser("serve", help="web chat UI + JSON API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("mcp", help="MCP server over stdio (tools + every agent as a prompt)")
    a = p.parse_args(argv)

    if a.cmd == "mcp":  # stdout belongs to the MCP protocol: print nothing else
        from .mcp_server import build_server
        build_server(a.index).run()
        return 0
    llm = get_llm()
    print("[LIVE (Anthropic)]" if llm.live else
          "[OFFLINE: extractive stand-in — set ANTHROPIC_API_KEY for real answers and OCR]", file=sys.stderr)

    if a.cmd == "agents":
        for ag in load_all().values():
            print(f"{ag.division:12} {ag.name:32} {ag.model:8} {ag.description[:80]}")
    elif a.cmd == "ingest":
        base = Index.load(a.index) if a.append and Path(a.index).exists() else \
            Index(embedder=_optional("AGENTKIT_DENSE", Embedder))
        index, report = ingest(a.paths, llm, base, contextualize=a.contextualize)
        index.save(a.index)
        for r in report:
            q = f" ⚠ {r['quarantined']} quarantined (prompt injection)" if r["quarantined"] else ""
            print(f"{'✗' if r['error'] else '✓'} {r['source']}: {r['chunks']} chunks {r['methods']}{q} {r['error']}")
        print(f"Index: {len(index.chunks)} chunks → {a.index}")
    elif a.cmd == "test-agents":
        rep = run_smoke(llm)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(to_markdown("Agent smoke tests", rep), encoding="utf-8")
        print(f"{rep['passed']}/{rep['total']} agents passed → {a.out}" +
              (f"  missing tests: {rep['missing']}" if rep["missing"] else ""))
        return 0 if rep["passed"] == rep["total"] and not rep["missing"] else 1
    elif a.cmd == "serve":
        from .web import serve
        serve(make_chat(a.index, llm), a.host, a.port)
    else:
        chat = make_chat(a.index, llm)
        if a.cmd == "ask":
            ans = chat.ask(a.question)
            if a.debug:
                print(json.dumps({k: v for k, v in ans.to_dict().items() if k != "answer"}, ensure_ascii=False,
                                 indent=1))
            print(ans.render())
        elif a.cmd == "chat":
            print("AUC Library assistant — ask in English, Arabic or Franco-Arabic. Type 'exit' to quit.")
            history = []
            while (q := input("\n> ").strip()).lower() not in ("exit", "quit", ""):
                ans = chat.ask(q, history)
                history += [{"role": "user", "content": q}, {"role": "assistant", "content": ans.text}]
                print(f"\n({ans.agent} · {ans.mode})\n{ans.render()}")
        elif a.cmd == "eval":
            rep = run_golden(chat)
            for path, text in ((a.out, to_markdown("AUC Library golden eval", rep)),
                               (a.json, json.dumps(rep, ensure_ascii=False, indent=1))):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(text, encoding="utf-8")
            print(f"{rep['passed']}/{rep['total']} passed · recall@{rep['k']} {rep['recall_at_k']} · "
                  f"MRR {rep['mrr']} · {rep['by_lang']} → {a.out}")
            if rep["pass_rate"] < a.min_pass or rep["recall_at_k"] < a.min_recall:
                print(f"✗ below thresholds (pass ≥ {a.min_pass}, recall ≥ {a.min_recall})", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
