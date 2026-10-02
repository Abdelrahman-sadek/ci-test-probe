"""agentkit CLI: agents · ingest · review · approve · ask · chat · eval · redteam · test-agents · serve · mcp ·
export-accessible · purge-logs"""
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
from .cache import AnswerCache
from .rag import Embedder, Index, Reranker, approve, ingest

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


def _is_sqlite(path: str) -> bool:
    return str(path).endswith((".db", ".sqlite", ".sqlite3"))


def _vectors(embedder):
    if not (embedder and os.getenv("AGENTKIT_QDRANT_URL")):
        return None
    from .vector import QdrantVectors
    return QdrantVectors(len(embedder.embed(["dimension probe"])[0]))


def open_index(path: str = DEFAULT_INDEX, create: bool = False):
    """JSON file → in-memory Index; .db/.sqlite → SqliteIndex (FTS5, optional Qdrant vectors)."""
    embedder = _optional("AGENTKIT_DENSE", Embedder)
    reranker = _optional("AGENTKIT_RERANK", Reranker)
    if _is_sqlite(path):
        if not create and not Path(path).exists():
            raise SetupError(f"No index at {path}. Run: agentkit --index {path} ingest {SEED_CORPUS}")
        from .store_sqlite import SqliteIndex
        return SqliteIndex(path, embedder, reranker, _vectors(embedder))
    if Path(path).exists():
        return Index.load(path, embedder=embedder, reranker=reranker)
    if create:
        return Index(embedder=embedder, reranker=reranker)
    raise SetupError(f"No index at {path}. Run: agentkit ingest {SEED_CORPUS}")


load_index = open_index


def make_chat(index_path: str = DEFAULT_INDEX, llm=None) -> LibraryChat:
    from .appdb import AppDB
    from .connectors import AlmaAccount, LibCal
    cache = None if os.getenv("AGENTKIT_CACHE") == "0" else AnswerCache()
    appdb = None if os.getenv("AGENTKIT_APP_DB") == "off" else AppDB()
    llm = llm or get_llm()
    chat = LibraryChat(open_index(index_path), llm, catalog=PrimoCatalog.from_env(),
                       log_path=os.getenv("AGENTKIT_LOG") or None, cache=cache, appdb=appdb,
                       libcal=LibCal.from_env(), account=AlmaAccount.from_env())
    if appdb is not None:
        from .budget import attach
        chat.budget = attach(llm, appdb)
    return chat


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
    s.add_argument("--fresh", action="store_true", help="start a new index instead of updating the existing one")
    s.add_argument("--force", action="store_true", help="re-parse files even if unchanged")
    s.add_argument("--review", action="store_true", help="hold changed sources for staff approval")
    s.add_argument("--contextualize", choices=["sync", "batch"], help="Contextual Retrieval (live mode)")
    s.add_argument("--workers", type=int, default=1, help="parse files in parallel (large archives)")
    sub.add_parser("tickets-check", help="escalate handoff tickets past the SLA (run from cron)")
    sub.add_parser("freshness", help="stale sources and notices about to expire")
    sub.add_parser("snapshots", help="list index snapshots")
    s = sub.add_parser("rollback", help="restore an index snapshot (default: the newest)")
    s.add_argument("name", nargs="?")
    s = sub.add_parser("export-searchable", help="add an invisible OCR text layer to a scanned PDF")
    s.add_argument("path")
    s.add_argument("-o", "--output", required=True)
    sub.add_parser("preflight", help="go/no-go checks before real users (exit 1 when anything blocks)")
    sub.add_parser("pilot-report", help="pilot metrics: deflection, handoffs, satisfaction, SLA")
    sub.add_parser("review", help="list sources waiting for approval")
    s = sub.add_parser("approve", help="release sources held for review")
    s.add_argument("origin", nargs="?", help="one source path (default: all pending)")
    s = sub.add_parser("redteam", help="run the security attack suite (evals/security/attacks.md)")
    s.add_argument("--out", default="data/redteam-report.md")
    s = sub.add_parser("export-accessible", help="export a document (OCR if needed) as accessible HTML")
    s.add_argument("path")
    s.add_argument("-o", "--output", required=True)
    s = sub.add_parser("feedback-report", help="unanswered + thumbs-down questions → golden-set candidates")
    s.add_argument("--out", default="evals/auc-library/candidates.md")
    s = sub.add_parser("purge-logs", help="apply log retention, or delete one user's rows (data-subject request)")
    s.add_argument("--user", help="delete every row of this user")
    s = sub.add_parser("ask", help="ask one question")
    s.add_argument("question")
    s.add_argument("--debug", action="store_true", help="show route, mode and retrieved chunks")
    sub.add_parser("chat", help="interactive chat (keeps follow-up context)")
    s = sub.add_parser("eval", help="golden questions: routing, retrieval, citations, facts")
    s.add_argument("--out", default="data/eval-report.md")
    s.add_argument("--json", default="data/eval-report.json")
    s.add_argument("--min-pass", type=float, default=0.0)
    s.add_argument("--min-recall", type=float, default=0.0)
    s.add_argument("--set", default="golden", choices=["golden", "dev", "heldout"], help="question set")
    s = sub.add_parser("test-agents", help="smoke-test every agent (live mode grades replies)")
    s.add_argument("--out", default="data/agents-report.md")
    s = sub.add_parser("serve", help="web chat UI + JSON API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-jobs", action="store_true", help="disable background ingestion (admin uploads)")
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
        if a.fresh and Path(a.index).exists():
            Path(a.index).unlink()
        base = open_index(a.index, create=True)
        base.snapshot(a.index)
        index, report = ingest(a.paths, llm, base, contextualize=a.contextualize or False, review=a.review,
                               force=a.force, workers=a.workers, checkpoint=lambda ix: ix.save(a.index))
        index.save(a.index)
        for r in report:
            q = f" ⚠ {r['quarantined']} quarantined (prompt injection)" if r["quarantined"] else ""
            print(f"{'✗' if r['error'] else '✓'} {r['source']}: {r['status'] or 'rejected'} · {r['chunks']} chunks "
                  f"{r['methods']}{q} {r['error']}")
        print(f"Index: {index.size} chunks → {a.index}")
    elif a.cmd in ("review", "approve"):
        index = open_index(a.index)
        if a.cmd == "review":
            pending = {o: e for o, e in index.manifest().items() if e.get("status") == "pending"}
            print("\n".join(f"{o}  ({e.get('url', '')})" for o, e in pending.items()) or "Nothing to review.")
        else:
            done = approve(index, a.origin)
            index.save(a.index)
            print(f"Approved {len(done)} source(s).")
    elif a.cmd == "export-accessible":
        from .accessible import export_html
        Path(a.output).write_text(export_html(a.path, llm), encoding="utf-8")
        print(f"Wrote {a.output}")
    elif a.cmd == "tickets-check":
        from .appdb import AppDB
        from .services import Handoff, escalate_overdue
        db = AppDB()
        done = escalate_overdue(db, Handoff(db))
        print(f"Escalated {len(done)} overdue ticket(s): {', '.join(done) or '-'}")
    elif a.cmd == "freshness":
        from .appdb import AppDB
        from .rag import freshness
        rep = freshness(open_index(a.index), AppDB())
        for s_ in rep["stale_sources"]:
            print(f"STALE  {s_['days']:>4} days  {s_['url'] or s_['origin']}")
        for n in rep["expiring_notices"]:
            print(f"NOTICE expires {n['valid_to']}  {n['title']}")
        print(f"{len(rep['stale_sources'])} stale source(s), {len(rep['expiring_notices'])} expiring notice(s).")
    elif a.cmd in ("snapshots", "rollback"):
        d = Path(a.index + ".snapshots")
        snaps = sorted(d.iterdir()) if d.exists() else []
        if a.cmd == "snapshots":
            print("\n".join(p_.name for p_ in snaps) or "No snapshots yet.")
        else:
            pick = next((p_ for p_ in snaps if p_.name == a.name), None) if a.name else (snaps[-1] if snaps else None)
            if not pick:
                raise SetupError("snapshot not found")
            data = pick.read_bytes()  # read first: the safety snapshot below may prune the oldest file
            try:  # the current state stays recoverable
                open_index(a.index, create=True).snapshot(a.index)
            except Exception:  # noqa: BLE001 — a corrupt index is the usual reason to roll back; keep its bytes
                if Path(a.index).exists():
                    Path(a.index + ".corrupt").write_bytes(Path(a.index).read_bytes())
            Path(a.index).write_bytes(data)
            for ext in ("-wal", "-shm"):
                Path(a.index + ext).unlink(missing_ok=True)
            print(f"Restored {pick.name} → {a.index}")
    elif a.cmd == "export-searchable":
        from .accessible import export_searchable_pdf
        print(export_searchable_pdf(a.path, llm, a.output))
    elif a.cmd == "pilot-report":
        from .appdb import AppDB
        from .security import SecureLog
        db = AppDB()
        rows = SecureLog(os.environ["AGENTKIT_LOG"]).read() if os.getenv("AGENTKIT_LOG") else []
        modes = {}
        for r in rows:
            modes[r["mode"]] = modes.get(r["mode"], 0) + 1
        fb, total = db.feedback_report(), max(len(rows), 1)
        rated = fb["up"] + fb["down"]
        print(json.dumps({"questions": len(rows), "modes": modes,
                          "deflection_rate": round((modes.get("answer", 0) + modes.get("account", 0)) / total, 3),
                          "handoff_rate": round(modes.get("handoff", 0) / total, 3),
                          "satisfaction": round(fb["up"] / rated, 3) if rated else None,
                          "thumbs_down_reasons": [x["reason"] for x in fb["thumbs_down"] if x["reason"]][:20],
                          "tickets": len(db.tickets()), "overdue_tickets": len(db.overdue_tickets()),
                          "workload": db.workload()}, ensure_ascii=False, indent=1))
        from .appdb import AppDB
        cands = AppDB().eval_candidates()
        rows = ["# Golden-set candidates (from unanswered and thumbs-down questions)", "",
                "Review each, add the right source page, then move it to golden-questions.md.", "",
                "| question | lang | seen as |", "|---|---|---|"]
        rows += [f"| {c['question'].replace('|', '/')} | {c['lang']} | {c['seen_as']} |" for c in cands]
        Path(a.out).write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"{len(cands)} candidate(s) → {a.out}")
    elif a.cmd == "purge-logs":
        from .appdb import AppDB
        from .security import SecureLog
        removed = AppDB().purge() if not a.user else 0
        if os.getenv("AGENTKIT_LOG"):
            removed += SecureLog(os.environ["AGENTKIT_LOG"]).purge(user=a.user)
        print(f"Removed {removed} row(s).")
    elif a.cmd == "test-agents":
        rep = run_smoke(llm)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(to_markdown("Agent smoke tests", rep), encoding="utf-8")
        print(f"{rep['passed']}/{rep['total']} agents passed → {a.out}" +
              (f"  missing tests: {rep['missing']}" if rep["missing"] else ""))
        return 0 if rep["passed"] == rep["total"] and not rep["missing"] else 1
    elif a.cmd == "preflight":
        from .preflight import check
        res = check(open_index(a.index))
        for b in res["blocking"]:
            print(f"✗ {b}")
        for w in res["warnings"]:
            print(f"! {w}")
        print("✓ ready for real users" if res["ok"] else f"{len(res['blocking'])} blocking problem(s)")
        return 0 if res["ok"] else 1
    elif a.cmd == "serve":
        from .api import serve
        from .jobs import JobQueue
        chat = make_chat(a.index, llm)
        if os.getenv("AGENTKIT_PILOT") == "1":  # real users: refuse to start until preflight passes
            from .preflight import check
            res = check(chat.index)
            if not res["ok"]:
                raise SetupError("preflight failed (AGENTKIT_PILOT=1):\n- " + "\n- ".join(res["blocking"]))
        jobs = None if a.no_jobs else JobQueue(chat.index, llm, a.index)
        serve(chat, a.host, a.port, jobs)
    elif a.cmd == "redteam":
        from .redteam import run_redteam
        rep = run_redteam(make_chat(a.index, llm))
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(to_markdown("Security red-team", rep), encoding="utf-8")
        print(f"{rep['passed']}/{rep['total']} attacks blocked → {a.out}")
        return 0 if rep["passed"] == rep["total"] else 1
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
            from . import ROOT
            qset = ROOT / "evals/auc-library" / {"golden": "golden-questions.md", "dev": "dev.md",
                                                  "heldout": "heldout.md"}[a.set]
            rep = run_golden(chat, qset)
            if a.set != "golden":
                a.out = a.out.replace("eval-report", f"{a.set}-report")
                a.json = a.json.replace("eval-report", f"{a.set}-report")
            for path, text in ((a.out, to_markdown(f"AUC Library {a.set} eval", rep)),
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
