# Evaluation log

Every review round and every evaluation run is recorded here with its exact numbers, including the bad ones. Offline runs use the deterministic extractive stand-in, not Claude, so answer-level scores are a floor: retrieval numbers carry over to live mode, sentence choice does not.

How to reproduce:
```bash
agentkit --index data/index.db ingest knowledge/auc-library/pages
agentkit --index data/index.db eval --set golden    # also: --set dev, --set heldout
agentkit redteam && agentkit test-agents && pytest -q && python scripts/ocr_bench.py
```

## Local model pool and README (2026-10-02)

- `AGENTKIT_LLM=local` runs the whole pipeline on self-hosted OpenAI-compatible servers. Several models can run at once with roles (answer, fast, vision), language and agent preferences, priorities and failover (`config/local-models.example.json`).
- Tests use scripted servers:
  - routing by role and language (an Arabic question goes to the Arabic server);
  - failover to the next server, then to search results;
  - invalid citation numbers dropped;
  - the critic catches a fabricated number from a local model.
  - No GPU here, so local answer quality is not measured yet: run the four eval sets on the chosen models first.
- README rewritten with 19 screenshots (`scripts/screenshots.py`), server tiers from measured numbers (app 80 MB RSS; search p95 40 ms at 20k chunks on 4 vCPU), local-model sizing and a research note on decision models ([plan 8](plans/08-decision-models.md)). Of the decision-model projects suggested to us, open-jev exists but ships untrained weights, and "Cloudflare Clef" could not be found.
- Tests: 198 passed.

## Plan 7: dashboard, costs, gaps, citations, critic, semantic cache, theses (2026-10-02)

All five phases of [plan 7](plans/07-analytics-governance-research.md) are built and tested (offline stand-in; no API key here).

| Set | JSON | SQLite | Note |
|---|---|---|---|
| golden | 40/40, recall 1.0, MRR 1.0 | 40/40, recall 1.0, MRR 1.0 | unchanged; also 40/40 with the critic on and with the semantic cache on |
| dev | 20/25, recall 1.0, MRR 0.947 | 20/25, recall 1.0, MRR 0.895 | unchanged |
| held-out | 16/20, recall 1.0, MRR 0.969 | 16/20, recall 0.938, MRR 0.938 | unchanged |
| **theses (new, 30 rows)** | first run **20/30, recall 0.778** → 24/30, recall 0.893 | first run **19/30, recall 0.741** → 24/30, recall 0.893 | target 0.9 not met offline |

Thesis set, honestly: the first run exposed three general problems, fixed before the second run. (1) Advisor names did not match across "First Last" and "Last, First". (2) The word "thesis" counted as evidence, so "a thesis on banking regulation" matched an unrelated thesis. (3) There were no Arabic or Franco words for degrees and departments. One expectation changed because it was wrong: t29 (a faculty article "in the repository") is correctly answered from the Knowledge Fountain page. The 6 remaining misses:
- t21, t24 and t26: Arabic or Franco topic words with no English counterpart offline. Cross-language retrieval needs BGE-M3 vectors (`AGENTKIT_DENSE=1`) or the live rewrite step.
- t19 and t20: the extractive stand-in quotes the abstract instead of the record line, though the right thesis is retrieved.
- t28: a thesis that does not exist gets a search strategy instead of a handoff. Nothing was invented.

| Phase | Evidence |
|---|---|
| A: dashboard and costs | 14 tabs in 5 groups. Every tab renders with data and passes axe with 0 violations in Chromium; the keyboard tablist works. Spend survives a restart. Thresholds alert once. The soft brake drops grade calls; the cap switches to search-results-only. Per-plugin costs sum to the total. Roles: a viewer cannot open Operations, staff cannot open System. Staff actions are audited under pseudonyms |
| B: knowledge gaps | EN/AR/Franco questions about lost-book fines form one cluster. Clusters with fewer than 3 people are hidden. A gap creates a page task or test candidates |
| C: citations | golden-file tests for APA 7, MLA 9, BibTeX, RIS, EndNote and CSL-JSON across pages, theses and books; nothing invented; BibTeX escaping; endpoint drops unknown fields |
| D: critic and semantic cache | Critic: a fabricated number is revised; a failed revision becomes a handoff with source links; a low model score triggers a fix; research answers are held while checked. Semantic cache: a paraphrase hits. These miss: alumni vs. undergraduates, a different number, where vs. when, a different language, after an index change. Live-data answers are never stored. p95 hit under 15 ms in-process. The Redis path round-trips between two workers |
| E: theses | The OAI-PMH harvester resumes after an interruption. It skips deleted, embargoed and non-thesis records and refuses non-https, non-allowlisted and DTD-bearing responses. Filters by department, year and advisor are identical on JSON and SQLite. Thesis answers cite APA "[Master's thesis, …]" from harvested metadata |

Bugs found while building: the Franco-Arabic branch of the plan 6 offline rewrite crashed (it joined a list as a string); a ticket payload field overwrote the ticket's kind; the first tablist markup broke ARIA rules (one tablist per group now). Checks: 191 tests pass, 1 skipped (optional screenshots); red-team 30/30; agents 20/20 (new `evaluator-critic`); bandit 0 medium/high; pip-audit 0; plugin validation passed.

## Agentic retrieval (plan 6, 2026-10-02)

Ideas taken from production-agentic-rag-course ([plan 6](plans/06-agentic-rag.md)): grade → rewrite → retry loop, per-request trace, model-free search.

| Set | JSON | SQLite | Change vs. before |
|---|---|---|---|
| golden | 40/40, recall 1.0, MRR 1.0 | 40/40, recall 1.0, MRR 1.0 | none |
| dev | 20/25, recall 1.0, MRR 0.947 | 20/25, recall 1.0, MRR 0.895 | none |
| held-out | 16/20, recall 1.0, MRR 0.969 | 16/20, recall 0.938, MRR 0.938 | none |
| red-team | 30/30 | — | none |

The existing sets did not move: their remaining misses are sentence choice, not failed retrieval. On misspelled probes the retry recovered 2 of 6 questions that were handoffs with one attempt ("dissertatoins onlin", "consultaion with a librarain"), on both back ends; the other 4 already succeeded through trigram matching. Out-of-scope questions (football, course registration) still hand off after the retry. The live-mode grader is covered by tests with a scripted model; its real effect needs the live eval. Tests: 154 passed (6 new).

## Round 3: final verdicts (2026-10-02)

**Sonnet: VERDICT: OK. Haiku: VERDICT: OK** (for entering the gated pilot). Both agreed nothing left is a P0: the code gates block real users until the live-model run and AUC's human sign-offs are done. Their remaining P1s were applied before this commit:

| Reviewer item | Change | Test |
|---|---|---|
| Eval reports could be stale on config (Sonnet) | Reports carry a fingerprint of models, agent prompts, guards, routes and style rules; preflight requires a match | `test_eval_must_match_current_config` |
| Pen test and DPO only in prose (Sonnet) | `signoff.json` records `security_review`, `dpo`, `staff_rota` (by, date); preflight blocks when any is empty | `test_preflight_*` |
| No kill switch (Sonnet) | `POST /admin/api/maintenance` or `AGENTKIT_MAINTENANCE=1`: every question gets a handoff and the cache is bypassed; drill in PILOT and RUNBOOK | `test_kill_switch_pauses_answers_and_cache` |
| Unverified-scan label in all channels (Sonnet) | The label is part of the answer text, which the web UI, widget and WhatsApp all render | `test_unchecked_scan_is_labelled` |
| Numeric OCR gate (Sonnet) | PILOT: mean CER ≤ 0.10 on non-diacritized print for real scans | — |
| Access review and key rotation (Sonnet) | RUNBOOK: monthly review, per-term rotation steps, log-key caveat | — |
| Live-eval failure remediation (Haiku) | RUNBOOK: per-check diagnosis and re-run steps | — |
| Thesis requests, related-topics upkeep (Haiku) | PILOT: expected behaviour and a content-upkeep table with owners | — |

Deferred, with reasons: persistent spend counter across restarts (P2; one process, daily budget); semantic related topics (P2; needs a larger corpus); figure-caption indexing (P2; behind `AGENTKIT_FIGURES`); real screen-reader, real-scan and live-model results (human and live runs, enforced as pilot gates).

Results (offline stand-in): golden 40/40 on JSON and SQLite; dev 20/25; held-out 16/20 (recall 1.0 JSON, 0.938 SQLite); red-team 30/30; agents 19/19; **148 tests pass**; bandit 0 medium/high. `agentkit preflight` in this checkout: 12 blocking problems, the expected result before any live run or sign-off.

## Round 2 → round 3 (2026-10-02)

Reviewer verdicts on round 2: **Sonnet: NEEDS WORK**, **Haiku: NEEDS WORK**. Both said the code is strong; the blockers are going live without a live-model eval, unverified content and corpus scope, and spoofable proxy identity. Changes:

| Reviewer item | Priority | Change | Evidence |
|---|---|---|---|
| Live-model eval before pilot (both) | P0 | Eval reports record `live` and `index_version`; `agentkit preflight` blocks unless golden ≥ 0.95 and held-out ≥ 0.8 ran live on the current index; `serve` with `AGENTKIT_PILOT=1` refuses to start otherwise | `test_preflight_*` |
| [VERIFY] facts and 8-page corpus (both) | P0 | Preflight blocks on any indexed [VERIFY] chunk, [VERIFY] in librarians/referrals, and a missing AUC sign-off (`signoff.json`: approver, date, scope, out of scope, verification owner and deadline) | `test_preflight_blocks…` |
| Proxy headers spoofable (Sonnet) | P0 | `AGENTKIT_PROXY_SECRET`: identity headers count only with the proxy's secret; Caddy strips client copies and adds it; preflight blocks proxy mode without it | `test_proxy_identity…` |
| Independent pen test, screen-reader users, staff rota, real scans (Sonnet) | P0/P1/P2 | Human launch requirements in PILOT.md with owners and checks; not claimable by code | — |
| Style/conflict edits could change facts (Sonnet) | P1 | Tests that cleanup keeps every number, URL, negation and citation (EN/AR); conflicts between different audiences (alumni vs. students) keep both | `test_style_cleanup_never_changes_facts`, `test_different_audiences…` |
| Citation precision (Sonnet) | P1 | New eval check `quoted`: every quote appears verbatim in the chunk it cites | golden 26/26, dev 19/19, held-out 16/16 |
| Low-confidence scans answered silently (Sonnet) | P1 | Answers citing an uncorrected OCR page below 0.7 confidence carry "scanned page that staff have not checked yet" | `test_unchecked_scan_is_labelled` |
| Erasure scope (Sonnet) | P1 | Privacy page (EN/AR) states what "Delete my data" removes and that help-desk/email copies follow library retention | — |
| Staff-facing metrics unclear (Haiku) | P1 | RUNBOOK "What staff see" table | — |
| Budget per process (Sonnet) | P2 | Documented: one server process per container | — |

Results after the changes (offline stand-in):
| Set | JSON | SQLite |
|---|---|---|
| golden | 40/40, recall 1.0, MRR 1.0 | 40/40, recall 1.0, MRR 1.0 |
| dev | 20/25, recall 1.0, MRR 0.947 | 20/25, recall 1.0, MRR 0.895 |
| held-out (round 2 set, now informed by fixes) | 16/20, recall 1.0, MRR 0.969 | 16/20, recall 0.938, MRR 0.938 |

Style check passes on every answer (30/30 golden, 20/20 dev, 17/17 held-out). Tests: 146 passed. `agentkit preflight` in this environment: **9 blocking problems** (no API key, golden and held-out evals offline, [VERIFY] in librarians and referrals, no sign-off, open sign-in, log key, log salt), which is the correct result for a development checkout.

## Round 1 → round 2 (2026-10-02)

### Question sets
| Set | Rows | Back end | Before fixes | After fixes | recall@5 | MRR |
|---|---|---|---|---|---|---|
| golden | 40 (23 EN, 12 AR, 5 Franco) | JSON | 40/40 | 40/40 | 1.0 | 1.0 |
| golden | 40 | SQLite FTS5 | 40/40 | 40/40 | 1.0 | 1.0 |
| dev (old held-out) | 25 | JSON | 12/25 (first and only blind run) | 20/25 | 1.0 | 0.947 |
| dev | 25 | SQLite FTS5 | — | 20/25 | 1.0 | 0.895 |
| **held-out (new)** | 20 | JSON | **11/20 (blind run)** | 16/20 | 0.875 → 1.0 | 0.844 → 0.969 |
| held-out | 20 | SQLite FTS5 | — | 16/20 | 0.938 | 0.938 |

The new held-out set was run once before any change: **11/20**. General fixes followed (integrity guard wording, research intent phrases, Egyptian and Franco words such as كارنيه, fat7a, emta, fetching the Arabic twin page when it ranks just below k, and two-sentence extractive answers). One expectation changed because it contradicted the project's own convention (x18 "write my literature review": refuse, like golden #22), not to fit the code. Because the set has now informed fixes, it becomes the next dev set and round 3 needs a fresh held-out set.

Remaining failures, all in the offline answerer's sentence choice, not retrieval:
- dev h1, h5, h7, h8, h17 and held-out x7, x8, x9, x15: the correct page is retrieved first (recall 1.0) but the extractive stand-in quotes a neighbouring sentence, e.g. "open for on-site use" instead of "open Sunday to Thursday". Claude reads the whole chunk, so these need a live run to judge.

### Other checks
| Check | Result |
|---|---|
| Unit, integration and browser tests | **137 passed** (16 new for round 1) |
| Red-team | 30/30 attacks blocked |
| Agent smoke tests | 19/19 |
| Accessibility (axe-core, WCAG 2.2 AA) | 0 violations on `/`, `/admin`, `/request`, `/privacy` |
| OCR bench (Tesseract, synthetic) | CER 0.000 on clean, rotated, blurred, noisy and low-res EN and AR; diacritized AR CER 0.24–0.81 with confidence 0.40–0.57, so every such page goes to the staff correction queue |
| bandit (medium/high) | 0 |
| pip-audit | 0 known vulnerabilities |
| `claude plugin validate .` | passed |
