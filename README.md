# 🤖 Agent Kit — lean AI agents for any use, with RAG, Chat & AUC Library divisions

A small, efficient set of Markdown agent definitions you can drop into **Claude Code, Cursor, Codex, Copilot, Gemini CLI, Aider** or use as system prompts in your own app.

Inspired by [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents), then cut down hard: **~30 lines per agent instead of ~200–300**. You get the same behaviour from far fewer tokens, and routing is more accurate. See [`docs/AGENT-DESIGN.md`](docs/AGENT-DESIGN.md).

## Roster

| Division | Agent | Use when… |
|---|---|---|
| 🧩 Core | `agent-architect` | you need a **new agent for any purpose** |
| | `orchestrator` | a task needs several agents |
| | `prompt-engineer` | a prompt is weak, inconsistent, or costs too much |
| | `evaluator` | you need to prove it works (golden tests) |
| 🔎 RAG | `rag-architect` | designing a RAG system end to end |
| | `rag-ingestion-engineer` | parsing, chunking, metadata, refresh |
| | `rag-retrieval-engineer` | retrieval returns wrong or missing chunks |
| | `rag-evaluator` | measuring recall, faithfulness, citations |
| | `ocr-document-engineer` | scanned or photographed PDFs, broken Arabic text layers |
| 💬 Chat | `chatbot-architect` | building a chat assistant |
| | `chat-guardrails` | injection, PII, scope, and escalation hardening |
| | `arabic-english-localizer` | Arabic (MSA/Egyptian/Arabizi) + English users |
| 🏛️ AUC Library | `auc-library-concierge` | front-door chat: hours, borrowing, access, services |
| | `auc-research-assistant` | finding sources, databases, search strings, citations |
| | `auc-catalog-navigator` | finding a specific item, call number, availability |
| | `auc-special-collections-guide` | rare books, archives, manuscripts, photos |
| | `auc-library-rag-builder` | building the knowledge base behind the AUC agents |

## Quick start

```bash
scripts/install.sh claude                 # all agents → ~/.claude/agents
scripts/install.sh claude --project rag   # only RAG agents → ./.claude/agents
scripts/install.sh cursor chat auc-library  # → ./.cursor/rules/*.mdc
scripts/install.sh agents-md              # → AGENTS.generated.md (Codex, Copilot, Gemini CLI, Aider…)
```
Then just ask: *"Use agent-architect to make me an agent that triages support emails."*

**In your own app:** load an agent file (minus the frontmatter) as the system prompt, and add retrieval and tools (see the RAG agents).

## 🧪 Test everything: OCR + RAG + AUC chat (runnable)

`agentkit/` is a small Python app that connects the agents into a working pipeline:

```
files (md/html/pdf/scans/images) ─► OCR (only pages with no text layer) ─► Arabic/English normalise
   ─► chunk by heading with a context header ─► BM25 index ─► guardrails ─► route to AUC agent
   ─► retrieve (+ Arabic/Franco-Arabic query expansion) ─► cited answer, or "I don't know"
```

```bash
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-...           # optional: without it, a fake LLM runs everything offline
python scripts/make_samples.py            # scanned PDF + Arabic PDF test files (already committed)
agentkit ingest samples/auc-library       # OCR + index → data/index.json
agentkit ask "Can alumni borrow books?" --debug
agentkit ask "المكتبة بتقفل الساعة كام النهارده؟"
agentkit chat                             # interactive
agentkit eval                             # 20 golden AUC questions → data/eval-report.md
agentkit test-agents                      # all 17 agents (live: each answers a prompt, Haiku grades it)
pytest -q                                 # 27 offline unit tests (OCR, Arabic, RAG, guardrails, routing)
```

| Step | What it handles |
|---|---|
| OCR (`agentkit/ocr.py`) | Detects scanned pages; uses Claude vision (best for Arabic and tables) or Tesseract `ara+eng` (`AGENTKIT_OCR=tesseract`) |
| Arabic (`agentkit/arabic.py`) | Converts PDF presentation forms (`ﻣﻮﺍﻋﻴﺪ`→`مواعيد`), unifies alef/yaa/taa marbuta, strips the article, detects Franco-Arabic |
| RAG (`agentkit/rag.py`) | Chunks by heading, deduplicates by content hash, keeps page and OCR-method metadata |
| Chat (`agentkit/chat.py`) | Guards against prompt injection, password requests, crisis messages, other universities and essay-writing; routes to the 4 AUC agents; checks citations |

Models: `AGENTKIT_MODEL` (default `claude-sonnet-5-5`) answers questions and runs OCR. `AGENTKIT_MODEL_FAST` (default Haiku 4.5) handles query rewriting and grading. The system prompt is sent with prompt caching to lower repeat cost.

> `samples/auc-library/` holds **made-up sample data** for testing. Replace it with official AUC pages.

## AUC Library chatbot: path to production

```
User (AR/EN/Arabizi) → auc-library-concierge ──┬─ auc-research-assistant
                                               ├─ auc-catalog-navigator   (live catalog tool)
                                               └─ auc-special-collections-guide
                 grounded by → RAG index of official AUC Library pages (auc-library-rag-builder)
```
1. **Verify facts.** Fill every `[VERIFY]` in [`knowledge/auc-library/facts.md`](knowledge/auc-library/facts.md) from official AUC Libraries pages. The agents are written so they will not state unverified facts as certain.
2. **Get permission** from AUC Libraries to crawl the site and use catalog/repository APIs. Then list the sources in [`sources.md`](knowledge/auc-library/sources.md).
3. **Build the index** with `auc-library-rag-builder`.
4. **Fill in and run** [`evals/auc-library/golden-questions.md`](evals/auc-library/golden-questions.md) with `rag-evaluator`. Target ≥ 90% faithfulness.
5. **Harden** the bot with `chat-guardrails` and `arabic-english-localizer` before launch.

> This is an independent project. It is not affiliated with or endorsed by The American University in Cairo.

## Repo layout
```
agents/<division>/*.md    agent definitions (Claude Code subagent format)
templates/                blank agent template
docs/AGENT-DESIGN.md      how to write efficient agents
knowledge/                domain facts (kept out of the agents so they stay current)
agentkit/                 runnable OCR + RAG + chat pipeline (Python)
samples/                  sample test documents (made up)
tests/                    pytest suite
evals/                    golden questions + per-agent smoke tests
scripts/                  install.sh, lint-agents.sh (CI-enforced)
```

## Add an agent
Copy `templates/agent.template.md` (or ask `agent-architect`), then run `scripts/lint-agents.sh`.

## License
MIT. The persona format is adapted from agency-agents (MIT, © msitarzewski).
