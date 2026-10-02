# Plan 7: analytics, governance, research integrations and the staff dashboard

Seven features in five phases. Each one lists what already exists, the design, the risks, and how we prove it works. Order: the dashboard and persistent usage data come first because four of the other features report into them.

| # | Feature | Phase | Size | Depends on |
|---|---|---|---|---|
| 7.1 | Staff dashboard with groups and tabs | A | L | — |
| 7.2 | Token and API cost dashboard with alerts | A | M | 7.1, usage table |
| 7.3 | Automated knowledge-gap detection | B | M | 7.1 |
| 7.4 | Reference and citation exporter | C | M | — (better after 7.7) |
| 7.5 | Evaluator-critic loop | D | M | quote checks (exist) |
| 7.6 | Semantic cache | D | M | embeddings (optional, exist) |
| 7.7 | Thesis and repository deep indexing | E | L | AUC permission, 7.4 |

Sizes: S ≈ 1–2 days, M ≈ 3–5 days, L ≈ 1–2 weeks, for one developer.

---

## 7.1 Staff dashboard with groups and tabs (Phase A)

**Today:** `/admin` is one long page with eight panels (access, upload, review, notices, tickets, feedback, OCR corrections, usage JSON). `/metrics`, `/healthz`, `agentkit preflight`, `freshness`, `pilot-report` and the per-answer `trace` hold more data, but staff cannot see most of it.

**Design.** One page, `/admin`, with five groups and accessible tabs (WAI-ARIA `tablist`; arrow keys move between tabs; English and Arabic, RTL). The last-opened tab is remembered per browser.

| Group | Tab | Shows |
|---|---|---|
| Overview | Today | questions, answered vs. handed off, satisfaction, open tickets, spend vs. budget, LLM status (ok/outage/budget), maintenance switch |
| Overview | Trends | 7/30/90-day lines: volume, deflection, handoff rate, 👍 rate, Arabic/Franco share |
| Quality | Conversations | redacted questions with mode, agent, sources, trace and feedback; filters by mode, agent, language, date |
| Quality | Knowledge gaps | clusters from 7.3 with counts, examples and "write a notice/page" actions |
| Quality | Evaluations | latest golden, dev and held-out reports (live or offline, config fingerprint), red-team, critic verdicts from 7.5 |
| Knowledge | Sources | every indexed source: owner, last ingested, chunk count, review status, freshness flag, [VERIFY] flag |
| Knowledge | Review and notices | the current review queue and the notices editor |
| Knowledge | OCR | correction queue, confidence distribution per collection, OCR-skipped pages |
| Service | Tickets | handoffs and rare-materials requests, SLA clock, workload per subject queue |
| Service | Librarians | subject queues, coverage rota, consultation bookings |
| Operations | Costs | the cost dashboard from 7.2 |
| Operations | Performance | p50/p95 latency per pipeline step (from traces), cache hit rate, rewrite and grade counts, errors |
| Operations | System | preflight checklist, snapshots and rollback, ingestion jobs, version, health of LibCal/Primo/Alma/LibAnswers |
| Operations | Security and privacy | rate-limit hits, blocked attacks by type, quarantined chunks, data-export/erase requests, admin actions log |

**Roles.** `viewer` (Overview, Quality read-only), `staff` (adds Knowledge and Service actions), `admin` (Operations, maintenance, rollback). Roles come from directory groups in `config/access.json`, as access levels do now.

**Build.**
- Data: one endpoint per tab, `GET /admin/api/dashboard/<tab>?from=&to=`, returning JSON aggregates computed from AppDB, the encrypted log, `METRICS` and eval reports. Nothing is computed in the browser beyond display.
- New AppDB tables: `answers` (id, ts, mode, agent, lang, cited sources, trace, degraded; question redacted and encrypted) and `admin_actions` (who, what, when). Today the log is append-only JSONL, which is slow to aggregate.
- Charts: inline SVG drawn by a small local script. The CSP forbids CDNs, and SVG keeps charts readable by screen readers through `<title>` elements and a table fallback under each chart.
- Live tiles refresh every 30 s using fetch with ETag, not websockets (simpler behind Caddy).

**Privacy.** Questions are shown only redacted. Staff never see user identities, only pseudonyms. Each conversation view is written to `admin_actions`.

**Done when:**
- axe shows 0 violations on every tab, English and Arabic.
- Keyboard-only use works.
- Role tests: a viewer cannot reach Operations.
- p95 for each tab endpoint is under 300 ms with 100k logged answers.
- Every number on Overview matches `agentkit pilot-report`.

---

## 7.2 Token and API cost dashboard with alerts (Phase A)

**Today:** `METRICS` counts tokens and cost per model in memory, so the counts reset on restart. `ResilientLLM` switches to extractive answers at `AGENTKIT_DAILY_BUDGET_USD`. There is no breakdown by feature and no warning before the switch.

**Design.**
- **Persistent usage.** A new AppDB table `llm_usage` (ts, model, input/output/cache tokens, cost, plugin, agent, purpose). Every model call records one row. `purpose` is one of answer, grade, rewrite, contextualize, ocr, critic, eval. Spend survives restarts, so the daily cap becomes a real cap.
- **Attribution.**
  - `agent` is the routed agent.
  - `plugin` is that agent's folder (`plugins/<division>`): core, rag, chat or auc-library.
  - OCR and contextualisation count as rag.
  - Guard refusals cost nothing.
- **Dashboard (Costs tab).**
  - Today's and this month's spend against budget.
  - Cost per answered question.
  - Stacked bars per plugin and per purpose.
  - Top agents by cost.
  - Cache savings, priced as the calls that 7.6 avoided.
  - A forecast of month-end spend from the 7-day average.
- **Alerts.**
  - Thresholds at 50 %, 80 % and 95 % of the daily and monthly budget (`AGENTKIT_BUDGET_ALERTS=50,80,95`). Each fires once per period by email (escalation mailbox) and optionally by webhook (Teams or Slack).
  - At 80 %, a soft brake: grading and rewriting turn off before answers degrade, since they are the cheapest calls to give up.
  - At 100 %, the existing switch to search-results-only answers.

**Done when:**
- Restarting the server keeps today's spend.
- Simulated spend crosses each threshold exactly once.
- The soft brake drops grade and rewrite calls in a test.
- The per-plugin sum equals the total.

---

## 7.3 Automated knowledge-gap detection (Phase B)

**Today:** AppDB stores unanswered questions and 👎 feedback, and `agentkit feedback-report` lists them one by one. Nobody groups them.

**Design.**
- **Signals that count as a gap.**
  - The answer was a handoff because nothing matched.
  - The grader dropped every passage.
  - The rewrite retry failed.
  - A 👎 with a reason.
  - An answer citing a low-confidence OCR page.
  - A ticket staff closed as "not in our pages".
- **Clustering**, nightly with `agentkit gaps` or the scheduler.
  - With embeddings (`AGENTKIT_DENSE=1`): BGE-M3 vectors of the redacted questions, grouped by agglomerative clustering at cosine ≥ 0.80.
  - Without: character-trigram and stemmed-term Jaccard (both already in the code), so it also works offline.
  - Arabic, Franco and English versions of one need land in one cluster through the existing normalisation and transliteration.
- **Each cluster shows:**
  - A label: its most distinctive terms, or a fast-model summary in live mode.
  - Size, distinct users and trend, plus 3 example questions (redacted).
  - The nearest existing source, if any. Low similarity means "missing page"; medium means "page exists but is unclear".
  - A suggested owner from the content-owner table.
- **Actions:** "Draft notice" opens the notice editor prefilled; "Create page task" files a ticket to the owner; "Add to dev set" appends a row to `evals/auc-library/dev.md` for a later fix to be measured.
- **Privacy:** a cluster is shown only with at least 3 distinct users (k-anonymity). Raw questions never leave AppDB, and gap data follows the 30-day retention.

**Done when:**
- Seeded test questions ("fines for lost books" ×5 in EN/AR/Franco, plus noise) form one cluster labelled about fines, with suggested owner Access services.
- Clusters of 2 users stay hidden.
- After a page is added, the cluster's new signals stop.

---

## 7.4 Reference and citation exporter (Phase C)

**Today:** answers cite library pages with title, section and URL. Research answers suggest search strategies but do not export anything.

**Design.**
- **Formats:**
  - BibTeX (`@misc`, `@phdthesis`, `@mastersthesis`, `@online`).
  - RIS, which Zotero, Mendeley and EndNote import.
  - EndNote tagged (`.enw`).
  - APA 7 and MLA 9 as formatted text.
  - CSL-JSON for anything else.
- **Where:**
  - An "Export references" menu under every answer with sources: download one file, or copy formatted text.
  - A "Cite all" button for saved items.
  - The API: `GET /api/cite?ids=…&format=…`.
- **Source metadata:**
  - Pages: title, publisher (AUC Libraries), URL, accessed date, last updated.
  - Theses (after 7.7): author, advisor, degree, department, year and the repository handle or DOI.
  - Catalog items (Primo): author, title, edition, publisher, year, ISBN and call number.
  - Missing fields are left out, never invented, and the UI says "check before submitting".
- **Implementation:** small formatters in `agentkit/citations.py`, with no new dependency. The APA and MLA rules for these item types are short; `citeproc-py` with official CSL styles is an option if more styles are needed.
- **Arabic:** keep Arabic titles in Arabic script, and add a transliterated title field when the source provides one.

**Done when:**
- Golden-file tests per format and item type.
- RIS and BibTeX import cleanly into Zotero, checked once by hand and recorded.
- The export buttons pass axe and have accessible names.
- No export contains a field the source did not provide.

---

## 7.5 Evaluator-critic loop (Phase D)

**Today:**
- Deterministic checks run in evals only: verbatim quotes, citation match, style.
- The live faithfulness judge runs in evals only.
- The `evaluator` agent exists in `plugins/core`.

**Design.**
- **Scope:** only research and special-collections answers, plus any answer with three or more sources or a policy claim (numbers, eligibility, fees). Short front-desk answers skip it to keep latency and cost down.
- **Two stages:**
  1. **Deterministic, every in-scope answer, about 1 ms:**
     - every quote is verbatim in its cited chunk;
     - every number in the answer appears in a cited quote;
     - no uncited policy claim;
     - no URL that is not in the sources;
     - the style filter.
  2. **Model critic, a fast model, only when stage 1 passes and the answer is in scope:** scores faithfulness, policy compliance (against `knowledge/auc-library/facts.md` and notices) and completeness from 1 to 5, as JSON.
- **Outcomes:**
  - pass: show the answer;
  - fixable: one revision with the critic's notes, then re-check;
  - fail: replace with a handoff, keeping the sources as links.
  - Verdicts are stored, and shown in Quality › Evaluations.
- **Streaming:** text already shown cannot be taken back. In-scope answers are generated in full and checked, then streamed out quickly with a "checking sources…" indicator. Out-of-scope answers stream as today. This adds roughly one fast-model call, about 0.5–1.5 s, to in-scope answers only.
- **Agent:** a new `evaluator-critic` agent in `plugins/core`, with a smoke test in `evals/agents/smoke.json`, so the same rules serve Claude Code users and the app.
- **Cost guard:** critic calls are tagged `purpose=critic` in 7.2 and turn off under the soft brake.

**Done when:**
- Scripted cases are caught:
  - a fabricated number becomes fixable, then passes or turns into a handoff;
  - a wrong URL fails;
  - a correct answer passes.
- The live eval shows the faithfulness pass rate up and no rise in false handoffs; this needs the live key.
- p95 latency for in-scope answers is within +1.5 s.

---

## 7.6 Semantic cache (Phase D)

**Today:** an in-process exact-match cache keyed by normalised question, index version and access level. Live data and account answers are never cached.

**Design.**
- **Lookup order:**
  1. exact key (today, under 1 ms);
  2. semantic match: embed the question and find the nearest cached question with cosine ≥ 0.95;
  3. then the full pipeline.
- **Safety rules.** Similar questions can need different answers ("Can alumni borrow?" vs. "Can undergraduates borrow?"). A semantic hit is used only when all of these hold:
  - the same index version and access level;
  - the same audience terms (the existing `_audiences` helper);
  - the same numbers and named entities;
  - the same language;
  - no live-data route.
  - Opening hours and room availability come from LibCal and are never cached; the cache can hold the static "where to find hours" text only.
- **Storage:** in-process vectors by default, which is enough for one server. With `AGENTKIT_REDIS_URL`, Redis with vector search, so several workers share it; entries expire with `AGENTKIT_CACHE_TTL` and on every index change.
- **Target:** under 15 ms p95 for a semantic hit, embedding included. That means a small embedding model on CPU for this step (e.g. `multilingual-e5-small`) rather than BGE-M3, measured on the deployment machine.
- **Visibility:** hit rate, latency and money saved in Operations › Performance and Costs.

**Done when:**
- A paraphrase pair hits.
- An alumni/undergraduate pair misses.
- A changed number misses.
- An hours question never hits.
- The cache empties after ingest.
- A benchmark records p95 hit latency, and the golden set still passes 40/40 with the cache warm.

---

## 7.7 Thesis and repository deep indexing (Phase E)

**Today:** Knowledge Fountain is one seed page that explains how to search it. Thesis full text is out of the signed-off scope.

**Prerequisites (not code):**
- written AUC permission to harvest and index full text;
- a rights policy: open access only, embargoes respected;
- updated `signoff.json` scope.

**Design.**
- **Harvest:**
  - OAI-PMH from the Digital Commons endpoint (`fount.aucegypt.edu`, endpoint path [VERIFY]), using `oai_dc` or `qualified-dc`.
  - Incremental by `from=` date with resumption tokens; checkpoints use the existing ingest checkpointing.
  - A nightly job: `agentkit harvest-theses`.
- **Metadata:**
  - title, author, advisor (`dc.contributor`), degree, department or school (set or `dc.publisher`), year, language, subjects, abstract, rights, PDF URL and handle.
  - Stored on each chunk in a new `meta` field. The SQLite back end gains indexed columns for department, year and advisor so filters run inside the query; Qdrant gets payload filters.
- **Text:**
  - The PDF goes through the existing safe parser and OCR (page caps and confidence apply).
  - Chunks are section-aware (abstract, chapters, conclusion), and every chunk carries the thesis title and author so citations stay exact.
- **Search:**
  - Filters are parsed from the question ("theses in economics since 2018 supervised by …") and from UI chips.
  - `GET /api/search` gains `department=`, `year_from=`, `year_to=` and `advisor=`.
  - Answers cite the thesis and page, and export through 7.4.
- **Scale:** thousands of theses, about 100 pages each, means hundreds of thousands of chunks.
  - Turn on dense retrieval with Qdrant (existing adapter).
  - Contextualise through the Batch API (existing).
  - Cap OCR per document.
  - Expect a one-off ingestion cost, estimated on a 100-thesis sample before the full run.

**Done when:**
- A 100-thesis sample harvests without duplicates and resumes after interruption.
- Filter tests by department, year and advisor.
- A new thesis-set eval (30 questions, EN/AR) is above 0.9 recall@5.
- Embargoed or restricted items are never indexed.

---

## Phases and milestones

| Phase | Weeks | Delivers | Exit check |
|---|---|---|---|
| A | 1–3 | AppDB `answers`, `llm_usage`, `admin_actions`; tabbed dashboard; cost tab and alerts | axe 0 on all tabs; restart keeps spend; alert thresholds fire once |
| B | 4 | Knowledge gaps with actions | seeded cluster test; k-anonymity test |
| C | 5–6 | Citation exporter | golden files per format; Zotero import recorded |
| D | 7–9 | Critic loop; semantic cache | scripted critic cases; cache safety cases; golden 40/40 with cache warm; live eval with critic |
| E | 10–12 | Thesis harvesting, metadata filters, thesis eval set | permission on file; 100-thesis sample; thesis eval ≥ 0.9 recall@5 |

Every phase ends the way the earlier rounds did:
1. Run the lint, tests, red-team, axe and bandit checks.
2. Run the evals on both back ends.
3. Write an entry in `docs/EVALUATION-LOG.md` with the numbers.
4. Push to both repositories.

New settings go into `.env.example`, and preflight gains checks where a feature affects users: a budget-alert recipient, the critic enabled for research answers, and harvest permission recorded.

## Risks
| Risk | Mitigation |
|---|---|
| A semantic cache serves the wrong policy to a similar question | strict match rules above; high threshold; tests with audience and number pairs; off by default until the live eval passes |
| Critic adds latency and cost | in-scope answers only; fast model; soft brake; measured in 7.2 |
| Dashboard exposes personal data | redacted questions only; pseudonyms; k-anonymity on clusters; role checks; audit log |
| Thesis rights and embargoes | harvest open-access items only; rights field checked per record; permission recorded in `signoff.json` |
| Generated citations contain mistakes | only fields the source provides; "check before submitting" note; golden-file tests |
