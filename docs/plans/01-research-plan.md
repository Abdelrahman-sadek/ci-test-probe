# Plan 1 — Research Plan

**Goal:** find what the best current projects, standards and papers do for (1) agent definitions, (2) RAG, (3) Arabic OCR and retrieval, (4) library chatbots, and (5) AUC Libraries' real, public facts. Then turn the evidence into an enhancement plan ([`02-enhancement-plan.md`](02-enhancement-plan.md)).

**Rules for the research**
- Prefer primary sources: official docs, papers, the repos themselves. Record every claim we rely on with its URL in [`../research/FINDINGS.md`](../research/FINDINGS.md).
- Only adopt a technique when it has evidence (benchmark, paper, or wide adoption) **and** fits our constraints: lean, cheap to run, works offline, Arabic + English.
- AUC facts: only from official `aucegypt.edu` pages, each recorded with the date it was retrieved. Anything else stays `[VERIFY]`.
- Budget: about 25 searches run in parallel batches, plus targeted page fetches. Stop a track once it has an answer.

## Research tracks

| # | Track | Questions | Where to look | Done when |
|---|---|---|---|---|
| R1 | Agent formats & distribution | Which frontmatter fields do Claude Code subagents support now? How do plugin marketplaces, Agent Skills (`SKILL.md`) and `AGENTS.md` work? | Claude Code docs, agentskills.io, agents.md | We know the exact fields and file layout to ship |
| R2 | Leading agent collections on GitHub | What do the most-starred subagent collections do better (structure, install, model tiers, validation)? | Web search `site:github.com` | 3+ concrete ideas worth adopting |
| R3 | RAG state of the art | Contextual retrieval, hybrid search with RRF, reranking, native citations in the Claude API | Anthropic engineering posts, Claude API docs, papers | A ranked list of retrieval upgrades with measured gains |
| R4 | Arabic retrieval | Best multilingual/Arabic embeddings and rerankers; normalisation and stemming; Franco-Arabic | MTEB, Hugging Face, camel-tools, papers | Model picks + normalisation rules we're missing |
| R5 | Arabic OCR | Which engines win on Arabic (benchmarks such as KITAB-Bench)? Practical tooling (OCRmyPDF, layout-aware PDF parsers) | Papers, GitHub | Engine recommendation by cost tier |
| R6 | Library systems & AUC | Library chatbot projects; Primo/Alma, LibCal, LibAnswers APIs; real AUC library facts (hours page, catalog, repository, special collections) | Vendor dev docs, `aucegypt.edu` | `facts.md` filled from sources; connector plan |
| R7 | Evaluation & safety | RAG metrics (RAGAS etc.); OWASP Top 10 for LLM apps; defences against indirect prompt injection | OWASP, RAGAS docs | Metrics to add + guardrail gaps |
| R8 | Integration surfaces | MCP server (Python SDK) so any MCP client can use the library search | MCP SDK docs (Context7) | Working tool design |

## Output
1. `docs/research/FINDINGS.md`: findings per track, each with sources.
2. `docs/plans/02-enhancement-plan.md`: prioritised work (P0/P1/P2), each item tied to a finding, with acceptance tests.
3. Implement P0 and P1, verify with lint, tests and evals, then commit.
