# Agent Kit: lean AI agents for any use, plus RAG, OCR, chat and AUC Library divisions

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
| core | `agent-architect` | inherit | you need a **new agent for any purpose** |
| | `orchestrator` | inherit | a task needs several agents |
| | `prompt-engineer` | inherit | a prompt is weak, inconsistent or costly |
| | `evaluator` | sonnet | you need to prove it works (golden tests, CI thresholds) |
| | `mcp-tool-builder` | inherit | an agent needs safe access to data or actions over MCP |
| rag | `rag-architect` | inherit | designing a RAG system (hybrid + RRF + rerank + citations) |
| | `ocr-document-engineer` | sonnet | scanned or photographed PDFs, broken Arabic text layers |
| | `rag-ingestion-engineer` | sonnet | sentence-aware chunking, metadata, injection quarantine |
| | `rag-retrieval-engineer` | inherit | retrieval returns wrong or missing chunks |
| | `rag-evaluator` | sonnet | recall@k, MRR, citations, faithfulness |
| chat | `chatbot-architect` | inherit | building a chat assistant |
| | `chat-guardrails` | inherit | hardening against the OWASP Top 10 for LLM apps |
| | `arabic-english-localizer` | sonnet | Arabic (MSA/Egyptian/Franco-Arabic) + English users |
| auc-library | `auc-library-concierge` | haiku | front desk: borrowing, access, hours, services |
| | `auc-research-assistant` | sonnet | sources, databases, search strings, citations |
| | `auc-catalog-navigator` | haiku | a specific item, call number, availability (live connector) |
| | `auc-special-collections-guide` | sonnet | rare books, archives, manuscripts, photographs |
| | `auc-library-rag-builder` | inherit | building the knowledge base behind the AUC agents |
| | `library-systems-integrator` | inherit | Primo/Alma, LibCal, LibAnswers, OAI-PMH connectors |

## Run the AUC Library assistant

```bash
cp .env.example .env && docker compose up -d   # HTTPS chat + staff page + API (see docs/DEPLOY.md)
```
or locally:
```bash
pip install -e ".[dev]"                                   # add ".[dense,qdrant]" for BGE-M3 + Qdrant
export ANTHROPIC_API_KEY=sk-...                           # optional: offline, a deterministic extractive stand-in runs everything
agentkit --index data/index.db ingest knowledge/auc-library/pages   # SQLite FTS5 index (or data/index.json in memory)
agentkit --index data/index.db serve                      # http://127.0.0.1:8000 · staff page /admin · metrics /metrics
agentkit --index data/index.db ask "ممكن الخريجين يستعيروا كتب؟" --debug
agentkit eval --min-pass 0.95 --min-recall 0.9            # 40 golden questions (--set dev | heldout)
agentkit feedback-report                                  # unanswered + 👎 questions → test-set candidates
agentkit redteam                                          # 30 attacks (EN/AR/Franco, direct + planted in documents)
agentkit export-accessible scan.pdf -o scan.html          # OCR → accessible HTML
pytest -q                                                 # 137 tests incl. a real-browser WCAG check
```

```
question ─► auth (OIDC/JWT · SSO proxy) ─► rate limit ─► guardrails ─► cache (index version × access level)
         ─► route to AUC agent ─► expand (glossary · Franco-Arabic→Arabic) ─► hybrid retrieval filtered by access
            (BM25 + trigram via SQLite FTS5 · optional BGE-M3/Qdrant · RRF · optional reranker) ─► relevance check
         ─► redact PII ─► Claude with native citations, streamed ─► answer · strategy · handoff · refuse ─► encrypted log
documents ─► allowlist ─► sandboxed parse (size/page/time/memory caps) ─► text layer, or OCR if scanned/garbled
          ─► Arabic glyph fix + normalisation ─► sentence chunks ─► injection quarantine ─► SHA-256 provenance
          ─► changed sources held for staff review ─► index        live data (Primo) ─► tool, never indexed
```

### Measured
| Check | Result |
|---|---|
| Golden questions (JSON and SQLite back ends) | **40/40**, recall@5 = 1.0, MRR = 1.0 (23 EN, 12 Egyptian/MSA Arabic, 5 Franco-Arabic) |
| Held-out questions, never tuned on (offline answerer) | **11/20** on the blind run, 16/20 after general fixes, recall@5 1.0; details in the [evaluation log](docs/EVALUATION-LOG.md) |
| OCR bench (Tesseract) | CER 0.000 on clean/rotated/blurred/noisy EN and AR; diacritized Arabic goes to the correction queue |
| Red-team (`agentkit redteam`) | **30/30** attacks blocked |
| Accessibility (axe-core in Chromium, WCAG 2.2 A/AA) | **0 violations** on chat, staff, request and privacy pages, English and Arabic RTL |
| Search latency (SQLite FTS5) | p95 **97 ms @ 20k** chunks, **232 ms @ 100k** |
| HTTP load (20 clients, offline LLM, cache off) | **218 req/s**, p95 105 ms, 0 errors |
| Supply chain | `pip-audit` 0 vulnerabilities · `bandit` 0 medium/high · hash-pinned lockfile · SBOM in CI |

### Security, scale and accessibility
| Area | What it does |
|---|---|
| Identity & access | OIDC/JWT (JWKS) or SSO-proxy sign-in; directory groups → document access levels (`config/access.json`); admin role |
| Data protection | Questions redacted before the model; pseudonymous, Fernet-encrypted logs; 30-day retention; per-user deletion; [data policy](docs/DATA-POLICY.md) |
| Poisoning & injection | https domain allowlist, SHA-256 provenance, review queue for changed sources, quarantine of instruction-like or exfiltrating text, sandboxed parsing |
| API hardening | Rate limits, body caps, strict CSP, security headers, admin-only staff APIs and metrics, HTTPS via Caddy |
| Scale | SQLite FTS5 back end, optional Qdrant, answer cache, streaming, background incremental ingestion, Batch API contextualisation, Prometheus metrics with cost |
| Accessibility | WCAG 2.2 AA chat, staff and request pages, full Arabic UI, voice input, embeddable widget, WhatsApp channel, accessible HTML export of scans |
| Library services ([plan 4](docs/plans/04-review-features.md)) | Feedback loop, saved chats/searches, live hours and rooms (LibCal), pinned notices + effective dates, real handoff tickets (LibAnswers/email), subject-librarian routing and consultations, read-only Alma account answers, finding aids (EAD) and rare-materials request form |
| OCR quality | Word-box layout (columns, RTL, tables), per-page confidence, image clean-up, two-pass Tesseract, handwriting notes, staff correction queue, view-the-scan links, searchable-PDF export |
| Resilience and operations ([plan 5](docs/plans/05-review-round1.md)) | Outage and budget fallback to search-results-only answers, handoff never lost, SLA escalation, index snapshots and rollback, freshness report, self-service data export and deletion |
| Answer style | [antislop](https://github.com/miqdadbadjuber/anti-slop) rules: no greetings, praise, closing offers or buzzwords in answers (EN and AR); checked in every eval |

Plans and evidence: [research](docs/research/FINDINGS.md) → [plan 2](docs/plans/02-enhancement-plan.md) → [plan 3: secure, scale, accessible](docs/plans/03-scale-secure-accessible.md). Operations: [deploy](docs/DEPLOY.md) · [pilot plan](docs/PILOT.md) · [runbook](docs/RUNBOOK.md) · [staff guide](docs/STAFF-GUIDE.md) · [data policy](docs/DATA-POLICY.md) · [العربية](README.ar.md).

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
agentkit/                       OCR → hybrid RAG → guarded chat · FastAPI service · CLI · MCP · connectors
agentkit/static/                accessible bilingual chat page, staff page, embeddable widget
config/                         access levels per group, ingestion domain allowlist
deploy/, Dockerfile, docker-compose.yml   container, Caddy TLS proxy, optional Qdrant
knowledge/auc-library/          fact sheet, source inventory, seed pages (official URLs)
evals/                          golden questions + per-agent smoke tests
samples/fixtures/               fictional docs + scanned/Arabic PDFs for tests
docs/                           agent design guide, research findings, plans
scripts/                        install.sh, lint-agents.sh (agents, skills, marketplace), make_samples.py
```

## Roadmap
OAI-PMH harvest of Knowledge Fountain · speech-to-text for WhatsApp voice notes · Postgres/pgvector back end for multi-writer deployments.

## License
MIT. The persona format is adapted from agency-agents (MIT, © msitarzewski).
