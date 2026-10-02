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
| Privacy notice reviewed (`/privacy`, `docs/DATA-POLICY.md`) | Data protection officer | signed off |

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

## Weekly loop
1. Run `agentkit pilot-report` and `agentkit feedback-report`.
2. Staff grade 50 random answers (25 Arabic or Franco).
3. Unanswered and 👎 questions become candidates; staff add the good ones to `evals/auc-library/dev.md`.
4. Fix content first (pages, notices), code second. Every code change re-runs golden and dev; held-out runs once per release.

## Stop or roll back
Stop the pilot (set `AGENTKIT_LIVE=0` or take the widget down) if an answer exposes private data, a security test fails, or correctness falls below 80 % in a week. Index problems roll back with `agentkit rollback`.

## Exit decision
Go to full launch when every target holds for the last two weeks. Otherwise extend by two weeks with a written list of fixes.
