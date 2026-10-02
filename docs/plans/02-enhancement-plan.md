# Plan 2 — Enhancement Plan

Built from [`../research/FINDINGS.md`](../research/FINDINGS.md). Principle: take the techniques with measured gains, keep the default install dependency-free and offline-testable, and make heavier options opt-in extras.

## Priorities

| ID | Pri | Enhancement | Why (finding) | Acceptance test |
|---|---|---|---|---|
| E1 | P0 | **Real AUC seed corpus.** `knowledge/auc-library/pages/*.md`, one page per official URL with a retrieval date and a verify note. The made-up docs move to `samples/fixtures/` and are used only in tests. `facts.md` is filled from sources. | R6: the borrowing, RBSCL and repository facts are now sourced | Bot answers "Can alumni borrow?" with *5 books / 14 days*, citing the official URL |
| E2 | P0 | **Arabic upgrades:** digit and Persian-letter normalisation, Light10 light stemmer, sentence-aware chunking (Arabic punctuation), Franco-Arabic→Arabic candidate transliteration | R4 | Unit tests: `١٥`≡`15`; `المكتبات`→`مكتب`; `maktaba`→`مكتبه`; Arabic sentence splits |
| E3 | P0 | **Hybrid retrieval with RRF:** BM25 (words) + character n-gram TF-IDF (subwords) fused with RRF (k=60). Optional dense embeddings (`[dense]`, BGE-M3) and cross-encoder reranking (`[rerank]`, bge-reranker-v2-m3). Fuzzy relevance check replaces the BM25 threshold. | R3, R4 | recall@5 ≥ 0.9 on answerable golden questions offline; out-of-scope still refused |
| E4 | P0 | **LLM layer to current API:** `claude-opus-5-5` main / `claude-haiku-4-5` fast; low `effort` for chat; refusal handling + server-side fallbacks; **native `search_result` citations**; **contextual retrieval** at ingest | R2/R3, R8 | Mock-client tests assert the request shape (search_result blocks, `citations.enabled`, fallbacks beta, no `effort` on Haiku) and citation parsing |
| E5 | P0 | **OWASP-mapped guardrails:** prompt-injection quarantine at ingestion (LLM01/04), access-level filtering (LLM08), PII redaction in logs (LLM02), input/output caps (LLM10), XSS-safe web output (LLM05) | R7 | A poisoned document is never retrieved; private chunk hidden from public users; emails/IDs redacted in the log |
| E6 | P0 | **Eval upgrade:** expectations `answer / strategy / handoff / refuse`, gold sources, recall@k + MRR, key-fact answer checks, JSON report, CI thresholds | R7 | `agentkit eval --min-pass 0.9 --min-recall 0.9` exits 0 offline; CI enforces it |
| E7 | P1 | **Claude Code plugin marketplace:** one plugin per division (core, rag, chat, auc-library) | R1 | `claude plugin validate .` passes; local install shows only that plugin's agents |
| E8 | P1 | **Portable Agent Skill** `skills/lean-agent-authoring/` (open SKILL.md standard), shipped in the core plugin | R1 | Lint validates the skill frontmatter; plugin details list the skill |
| E9 | P1 | **MCP server** `agentkit mcp`: `search_library` / `ask_library` / `list_agents` tools, and every agent as an MCP prompt (SDK v2 `MCPServer`, v1 fallback) | R8 | Test builds the server and lists its tools and prompts |
| E10 | P1 | **Web UI** `agentkit serve`: stdlib server, bilingual right-to-left chat page with sources, route and debug panel; JSON API | Usability ("test them all") | HTTP test: `POST /api/ask` returns a cited answer; body-size cap enforced |
| E11 | P1 | **Agent upgrades:** research-backed rules (RRF, contextual retrieval, OCR tiers, OWASP), model tiers + `color` + `maxTurns`; new agents `mcp-tool-builder` and `library-systems-integrator`; follow-up questions (conversation memory) in chat | R1–R8 | Lint passes for all agents; a smoke test exists for each |
| E12 | P1 | **Lint upgrade:** allowed `model`/`color` values, description budget, marketplace ↔ agents consistency, skill frontmatter | R1 | `scripts/lint-agents.sh` catches each class of error (unit-tested) |
| E13 | P1 | **Primo catalog connector** (live availability as a tool, never indexed). Enabled only when AUC provides `vid` and API key. | R6 | Mocked Primo JSON is turned into cited catalog results |
| E14 | P2 | Docs: architecture diagram, roadmap; CI matrix (Python 3.10/3.12) + plugin validation | — | CI green |

**Out of scope / later (documented in README roadmap):** vector database for corpora over ~50k chunks, LibCal hours connector, OAI-PMH harvesting of Knowledge Fountain (endpoint `[VERIFY]`), streaming responses, Docker image.

## Build order
E2 → E3 → E1 → E5 → E4 → E6 → E13 → E9/E10 → E11/E12 → E7/E8 → E14, running lint, tests and evals after each group. Push once everything is green.

## Risks & mitigations
- **No API key in this container:** live paths are verified with a mocked Anthropic client (request shapes and response parsing), not real calls. Say so in the report.
- **AUC facts from search extracts:** every seed page carries its URL, retrieval date and "confirm on the official page". Unknowns stay `[VERIFY]`.
- **Extras (torch for dense/rerank) are heavy:** keep them optional and test fusion with a stub embedder.
