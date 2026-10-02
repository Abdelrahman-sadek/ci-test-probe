# 🤖 Agent Kit — lean AI agents for any use, plus RAG, OCR, Chat & AUC Library divisions

19 small, CI-validated agent definitions and a runnable **OCR → hybrid RAG → guarded chat** app for The American University in Cairo (AUC) Libraries, in Arabic, English and Franco-Arabic.

- **Lean:** about 35 lines per agent, versus 200–300 in [agency-agents](https://github.com/msitarzewski/agency-agents) (the inspiration). Each division plugin costs **≈ 210–420 always-on tokens** in Claude Code.
- **Works everywhere:** a Claude Code plugin marketplace, plus installers for Cursor and `AGENTS.md` tools (Codex, Copilot, Gemini CLI, Aider), an open-standard Agent Skill, and an MCP server.
- **Research-backed:** every design choice links to evidence in [`docs/research/FINDINGS.md`](docs/research/FINDINGS.md) (plans: [research](docs/plans/01-research-plan.md) → [enhancements](docs/plans/02-enhancement-plan.md)).

## Install the agents

**Claude Code (plugins, recommended):** one plugin per division, so you load only what you need.
```
/plugin marketplace add <owner>/<repo>
/plugin install agent-kit-core@agent-kit          # build agents for any use (+ lean-agent-authoring skill)
/plugin install agent-kit-rag@agent-kit           # RAG + OCR
/plugin install agent-kit-chat@agent-kit          # chatbot design, guardrails, Arabic
/plugin install agent-kit-auc-library@agent-kit   # AUC Libraries assistants
```
**Other tools:**
```bash
scripts/install.sh claude [--project] [core rag chat auc-library]   # copy into ~/.claude/agents (+ skills)
scripts/install.sh cursor rag chat                                  # → .cursor/rules/*.mdc
scripts/install.sh agents-md                                        # → AGENTS.generated.md
```
Then ask: *"Use agent-architect to make me an agent that triages support emails."*

## Roster

| Plugin | Agent | Model | Use when… |
|---|---|---|---|
| 🧩 core | `agent-architect` | inherit | you need a **new agent for any purpose** |
| | `orchestrator` | inherit | a task needs several agents |
| | `prompt-engineer` | inherit | a prompt is weak, inconsistent or costly |
| | `evaluator` | sonnet | you need to prove it works (golden tests, CI thresholds) |
| | `mcp-tool-builder` | inherit | an agent needs safe access to data or actions over MCP |
| 🔎 rag | `rag-architect` | inherit | designing a RAG system (hybrid + RRF + rerank + citations) |
| | `ocr-document-engineer` | sonnet | scanned or photographed PDFs, broken Arabic text layers |
| | `rag-ingestion-engineer` | sonnet | sentence-aware chunking, metadata, injection quarantine |
| | `rag-retrieval-engineer` | inherit | retrieval returns wrong or missing chunks |
| | `rag-evaluator` | sonnet | recall@k, MRR, citations, faithfulness |
| 💬 chat | `chatbot-architect` | inherit | building a chat assistant |
| | `chat-guardrails` | inherit | hardening against the OWASP Top 10 for LLM apps |
| | `arabic-english-localizer` | sonnet | Arabic (MSA/Egyptian/Franco-Arabic) + English users |
| 🏛️ auc-library | `auc-library-concierge` | haiku | front desk: borrowing, access, hours, services |
| | `auc-research-assistant` | sonnet | sources, databases, search strings, citations |
| | `auc-catalog-navigator` | haiku | a specific item, call number, availability (live connector) |
| | `auc-special-collections-guide` | sonnet | rare books, archives, manuscripts, photographs |
| | `auc-library-rag-builder` | inherit | building the knowledge base behind the AUC agents |
| | `library-systems-integrator` | inherit | Primo/Alma, LibCal, LibAnswers, OAI-PMH connectors |

## Run the AUC Library assistant (OCR + RAG + chat)

```
question ─► guardrails ─► route to AUC agent ─► expand (glossary · Franco-Arabic→Arabic · LLM keywords)
         ─► hybrid retrieval: BM25 + char 3-grams (+ BGE-M3) ─► RRF fusion (+ bge-reranker) ─► relevance check
         ─► Claude with native search_result citations ─► answer · strategy · handoff · refuse
documents ─► text layer or OCR (Claude vision / Tesseract) ─► Arabic normalisation ─► sentence-aware chunks
          ─► injection quarantine · access labels ─► index        live data (Primo catalog) ─► tool, never indexed
```

```bash
pip install -e ".[dev]"                        # add ".[dense]" for BGE-M3 + reranker
export ANTHROPIC_API_KEY=sk-...                # optional: offline, a deterministic extractive stand-in runs everything
agentkit ingest knowledge/auc-library/pages    # seed corpus from official AUC pages (+ any folder of PDFs/scans/HTML)
agentkit ask "Can alumni borrow books?" --debug
agentkit ask "ممكن الخريجين يستعيروا كتب؟"
agentkit chat                                  # terminal chat, keeps follow-up context
agentkit serve                                 # web chat at http://127.0.0.1:8000 (right-to-left aware, shows sources)
agentkit eval --min-pass 0.95 --min-recall 0.9 # 27 golden questions → data/eval-report.md/.json
agentkit test-agents                           # smoke-test all 19 agents (live: Haiku grades each reply)
claude mcp add agentkit -- agentkit --index "$PWD/data/index.json" mcp   # tools + every agent as an MCP prompt
pytest -q                                      # 62 tests: Arabic, OCR, RAG/RRF, guardrails, live-API shapes, MCP, web
```

**Offline results:** 27/27 golden questions pass; recall@5 = 1.0 and MRR = 1.0 in English, Arabic and Franco-Arabic.

| Area | What it does | Evidence |
|---|---|---|
| Arabic (`agentkit/arabic.py`) | NFKC (fixes PDF glyphs like `ﻣﻮﺍﻋﻴﺪ`), ٠-٩→0-9, Light10 stemming, sentence splitting on `؟ ؛`, Franco-Arabic→Arabic candidates | Larkey Light10; arXiv 2506.06339 |
| Retrieval (`agentkit/rag.py`) | BM25 + character n-grams (+ dense) fused with RRF k=60; optional cross-encoder rerank; contextual retrieval (`--contextualize`) | Anthropic Contextual Retrieval; RRF |
| Answers (`agentkit/llm.py`) | `claude-opus-5-5` at low effort, native `search_result` citations with quotes, refusal handling + server-side fallbacks; `claude-haiku-4-5` for rewriting and grading | Claude API docs |
| Safety (`agentkit/chat.py`, `web.py`) | Mapped to OWASP LLM 2025: injection quarantine (01/04), PII-redacted logs (02), safe output rendering + CSP (05), access filters (08), cited-only answers (09), size caps (10) | OWASP Top 10 for LLM |
| OCR (`agentkit/ocr.py`) | Text layer first, OCR only scanned pages; Claude vision by default (vision-language models beat classic OCR by ~60% CER on Arabic) | KITAB-Bench, QARI-OCR |

Models are configurable with `AGENTKIT_MODEL`, `AGENTKIT_MODEL_FAST` and `AGENTKIT_EFFORT`. Other settings: `AGENTKIT_DENSE=1` / `AGENTKIT_RERANK=1` (optional extras), `AGENTKIT_PRIMO_URL/VID/KEY` (live catalog), `AGENTKIT_LOG=data/chat.jsonl` (redacted log).

## AUC chatbot: path to production
1. **Confirm the facts.** [`knowledge/auc-library/facts.md`](knowledge/auc-library/facts.md) and the [seed pages](knowledge/auc-library/pages/) were paraphrased from search extracts of official pages, each with its URL. Confirm them, and fill every `[VERIFY]` (hours, catalog system, databases).
2. **Get permission** from AUC Libraries to crawl the site and use its APIs; list sources in [`sources.md`](knowledge/auc-library/sources.md).
3. **Ingest** the full site and scans: `agentkit ingest <crawl> --contextualize`. Turn on `AGENTKIT_DENSE=1` for large corpora.
4. **Connect live data:** set `AGENTKIT_PRIMO_*` once AUC confirms its discovery system.
5. **Gate releases** on `agentkit eval` in CI, and red-team with `chat-guardrails` and `arabic-english-localizer`.

> Independent project, not affiliated with or endorsed by The American University in Cairo. `samples/fixtures/` holds **fictional** test data only.

## Repo layout
```
plugins/<division>/agents/*.md  agents (Claude Code subagent format); each division is a plugin
plugins/core/skills/            lean-agent-authoring (open Agent Skills standard)
.claude-plugin/marketplace.json plugin marketplace (validated with `claude plugin validate .`)
agentkit/                       OCR → hybrid RAG → guarded chat · CLI · web · MCP · connectors
knowledge/auc-library/          fact sheet, source inventory, seed pages (official URLs)
evals/                          golden questions + per-agent smoke tests
samples/fixtures/               fictional docs + scanned/Arabic PDFs for tests
docs/                           agent design guide, research findings, plans
scripts/                        install.sh, lint-agents.sh (agents, skills, marketplace), make_samples.py
```

## Roadmap
Vector database for 50k+ chunks · LibCal hours connector · OAI-PMH harvest of Knowledge Fountain · streaming answers in the web UI · Docker image.

## License
MIT. The persona format is adapted from agency-agents (MIT, © msitarzewski).
