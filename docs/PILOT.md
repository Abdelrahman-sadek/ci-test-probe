# Pilot plan: AUC Library assistant

A four-week pilot answers one question: does the assistant resolve routine library questions correctly, and hand the rest to librarians faster than the current channels?

## Scope
- **Who:** one school's students and faculty (suggested: Humanities and Social Sciences, which use RBSCL and Arabic sources most), plus reference staff.
- **Where:** the chat page and the widget on one library web page. WhatsApp stays off until week 3.
- **What it answers:** borrowing, renewals, access, hours (LibCal), rare-books visits, theses (Knowledge Fountain), search strategy. Account answers only after Alma read access is approved.

## Before day 1
| Step | Owner | Check |
|---|---|---|
| Confirm every `[VERIFY]` fact in `knowledge/auc-library/` | Head of reference | `grep -r VERIFY knowledge/` returns only items marked out of scope |
| Ingest the approved crawl | Systems librarian | `agentkit eval --set golden` ≥ 95 %, `--set heldout` measured and recorded |
| Set `ANTHROPIC_API_KEY`, `AGENTKIT_DAILY_BUDGET_USD`, SSO, `AGENTKIT_LOG_KEY` | IT | `/healthz` shows `"llm": "ok"` |
| Fill `librarians.json`, `referrals.json` | Reference staff | each subject has a contact and queue |
| Privacy notice and retention reviewed (`/privacy`, `docs/DATA-POLICY.md`) | Data protection officer | signed off |
| Corpus scope signed (`knowledge/auc-library/signoff.json`: approver, date, in and out of scope, owner and deadline for [VERIFY] items) | Head of reference | `agentkit preflight` passes the sign-off check |
| Live-model evals: golden ≥ 95 %, held-out ≥ 80 %, 0 fabricated hours, fees or eligibility in 30 librarian-graded answers | Systems librarian + 2 librarians | `agentkit preflight` passes the eval checks |
| Independent security test of SSO, staff page and WhatsApp webhook | IT security | no open high findings |
| Screen-reader test with NVDA and VoiceOver, English and Arabic, by real users | Accessibility services | no blocking issues |
| Handoff coverage: named owner per queue, cover for closed hours and holidays, tested with real subject librarians | Head of reference | rota published |
| 20–30 real AUC scans with ground truth; compare Claude and Tesseract with `scripts/ocr_bench.py --gt-dir` | RBSCL + systems | mean CER ≤ 0.10 on non-diacritized print for the chosen engine; diacritized and handwritten pages corrected by staff before they are answered from; record the confidence distribution |
| Kill-switch drill (`/admin/api/maintenance`) | Systems librarian | paused within 5 minutes of the decision |

Gate: with `AGENTKIT_PILOT=1`, `agentkit serve` refuses to start until `agentkit preflight` passes.

## Metrics (`agentkit pilot-report`, weekly)
| Metric | Definition | Target |
|---|---|---|
| Deflection | answer + account modes ÷ all questions | ≥ 60 % |
| Correctness | staff-graded sample of 50 answers a week, fully correct and cited | ≥ 90 % |
| Unsafe answers | wrong policy stated as fact, or private data shown | 0 |
| Handoff time | ticket created → first librarian reply | median ≤ 1 working day; none past `AGENTKIT_TICKET_SLA_HOURS` |
| Satisfaction | 👍 ÷ rated answers | ≥ 80 % |
| Arabic parity | correctness for Arabic/Franco questions vs English | within 5 points |
| Cost | `agentkit_llm_cost_usd_total` per 1,000 questions | under the agreed budget |

## Expected behaviour for common requests
- **A specific thesis:** the assistant explains how to search AUC Knowledge Fountain (by school, department, author or keyword) and links it. With the Primo connector configured, the catalog navigator also returns live catalog results. It does not summarise thesis full text; requests to write or summarise graded work get the integrity reply.
- **Out of scope** (fees, interlibrary loan, database-specific rules): a handoff with the librarian button, or a referral to the campus office.

## Content upkeep during the pilot
| File | Who updates it | How |
|---|---|---|
| Pages under `knowledge/auc-library/pages/` | content owners (RUNBOOK) | edit, re-ingest, approve in `/admin` Review |
| Notices (closures, exam hours) | library communications | `/admin` Notices, with start and end dates |
| `related-topics.json` | reference librarian on rota | weekly: add topics suggested by unanswered and 👎 questions from `agentkit feedback-report` |
| `librarians.json`, `referrals.json` | head of reference | when contacts or queues change |

## Weekly loop
1. Run `agentkit pilot-report` and `agentkit feedback-report`.
2. Staff grade 50 random answers (25 Arabic or Franco).
3. Unanswered and 👎 questions become candidates; staff add the good ones to `evals/auc-library/dev.md`.
4. Fix content first (pages, notices), code second. Every code change re-runs golden and dev; held-out runs once per release.

## Stop or roll back
Stop the pilot (set `AGENTKIT_LIVE=0` or take the widget down) if an answer exposes private data, a security test fails, or correctness falls below 80 % in a week. Index problems roll back with `agentkit rollback`.

## Exit decision
Go to full launch when every target holds for the last two weeks. Otherwise extend by two weeks with a written list of fixes.
