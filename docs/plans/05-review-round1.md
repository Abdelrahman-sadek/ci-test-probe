# Plan 5: multi-model review, round 1

After the 12 features in [plan 4](04-review-features.md), two other models reviewed the project. Each got the full inventory and measured results, and each returned a gap list per part (chat, RAG, OCR, AUC assistant) and a verdict. Round 1 verdicts: **Haiku: OK**, **Sonnet: NEEDS WORK**. This plan records what they found and what changed.

## Gaps found and what changed
| Area | Gap (reviewer) | Change | Test |
|---|---|---|---|
| Evaluation | Held-out set was saturated by tuning (Sonnet) | Old held-out renamed `dev.md`; new `heldout.md` written and run once before any fix; `agentkit eval --set golden\|dev\|heldout` | eval reports |
| Chat | No plan for a model outage or a runaway bill (Sonnet) | `ResilientLLM`: circuit breaker, daily budget, extractive fallback with a "search results only" banner; `/healthz` reports `llm` | `test_outage_degrades…`, `test_budget…` |
| Chat | Policy pages can disagree after an update (Sonnet) | `resolve_conflicts` keeps the newer page and says so | `test_conflicting_sources…` |
| Chat | Dead ends for non-library questions (Haiku) | Referral to the right campus office; related-topic chips | `test_related_topics_and_referral` |
| Data rights | No self-service access or erasure (Sonnet) | `GET`/`DELETE /api/me/data`, `/privacy` page (axe-checked) | `test_data_export_and_delete`, a11y |
| Handoff | A failing LibAnswers or SMTP backend lost the request (Sonnet) | Per-backend try/except; the ticket is always saved and the user told | `test_handoff_backend_failure…` |
| Operations | No SLA or staffing view (Sonnet) | `overdue_tickets`, `escalate_overdue` (`agentkit tickets-check`), workload per queue in the staff page's Usage panel | `test_overdue_tickets…` |
| RAG | No undo for a bad ingest (Sonnet) | Snapshots before every ingest, `agentkit snapshots`/`rollback`; rollback works on a corrupt index | `test_snapshot…rollback` |
| RAG | Stale pages go unnoticed (Haiku) | `agentkit freshness` and `/admin/api/freshness` | `test_freshness…` |
| RAG | Large crawls ingest slowly and restart from zero (Sonnet) | `--workers`, checkpoints every 25 files | — |
| OCR | Cost of OCR on huge scans (Sonnet) | `AGENTKIT_OCR_MAX_PAGES_PER_DOC` cap, pages beyond it marked `ocr-skipped` | `test_ocr_page_cap` |
| OCR | Accuracy on noisy Arabic scans unmeasured (Sonnet) | `scripts/ocr_bench.py`; two-pass Tesseract and adaptive denoise | `test_diacritized_arabic…` |
| OCR | Scans not searchable outside the assistant (Haiku) | `agentkit export-searchable` adds an invisible text layer | `test_searchable_pdf…` |
| Launch | No pilot plan or runbook (both) | [PILOT.md](../PILOT.md), [RUNBOOK.md](../RUNBOOK.md) with owners, severities and security notes | — |

## Answer style (antislop)
The user asked to use [antislop](https://github.com/miqdadbadjuber/anti-slop). Its rules target generic AI output in UI, copy and code. Applied here:
- **Answers:** `agentkit/style.py` adds prompt rules (no greeting, praise or closing offer; no buzzwords; name who acts), removes filler opening and closing sentences in English and Arabic without touching cited sentences, and reports slop for evals. Every eval answer now has a `style` check.
- **Agents:** `scripts/lint_agents.py` rejects filler words in agent instructions; the design checklist says so.
- **Code and docs:** decorative banner comments became plain one-line comments; emoji removed from README headings.

## Bugs found while testing
- `rollback` crashed on a corrupt index, the main case it exists for. It now keeps the broken file as `<index>.corrupt` and restores.
- `rollback` could prune the snapshot it was about to restore when the safety snapshot exceeded `AGENTKIT_SNAPSHOTS`. It now reads the snapshot first.
- Referral missed "register for courses" (keyword was only "registration").

Results are in the [evaluation log](../EVALUATION-LOG.md).
