# Runbook: on-call and incidents

## Health checks
- `GET /healthz`: `{"status": "ok", "chunks": <n>, "index_version": "…", "llm": "ok" | "outage" | "budget"}`.
- `GET /metrics` (admin): `agentkit_llm_failures_total`, `agentkit_degraded_total`, `agentkit_handoff_failures_total`, latency per stage, cost.
- Daily cron: `agentkit tickets-check` (escalates overdue handoffs) and `agentkit freshness` (stale sources, expiring notices); mail the freshness output to the content owners.
- Before any release to real users: `agentkit preflight` must print "ready for real users". It blocks on [VERIFY] facts, missing AUC sign-off (`knowledge/auc-library/signoff.json`), evals not run live on the current index, open sign-in, spoofable proxy identity, unencrypted logs and the default log salt.

## What staff see
Every answer carries a `trace` (route, retrieve attempts, grade, rewrite, generate, in ms); `agentkit ask "…" --debug` prints it. `GET /api/search?q=` shows what retrieval finds without calling the model.

| Where | What |
|---|---|
| `/admin` Usage panel | questions by mode, feedback, degraded answers, overdue tickets, workload per subject queue (open, closed, oldest open in hours) |
| `/admin` Tickets | every handoff and rare-materials request with status; set answered, closed or escalated |
| `/admin` Corrections, Review | low-confidence OCR pages to correct; changed sources waiting for approval |
| `agentkit pilot-report` | weekly JSON for the pilot metrics in [PILOT.md](PILOT.md) |
| `/metrics` (Prometheus, admin) | for IT dashboards and alerts: failures, degraded answers, handoff failures, latency, cost |

## Kill switch
A harmful or wrong answer is spreading: pause answering at once, then investigate.
- Staff page or API: `POST /admin/api/maintenance {"on": true}` (admin). Every question gets a librarian handoff; cached answers are neither served nor stored. `{"on": false}` resumes.
- At start-up: `AGENTKIT_MAINTENANCE=1`.
- Drill it once before the pilot and once mid-pilot; record the time from decision to paused.

## If preflight fails on live evals
1. Open `data/eval-report.md` / `data/heldout-report.md`: each failing row names the failed check (route, retrieval, citation, quoted, facts, style, faithful).
2. Retrieval or citation failures: the page is missing or unclear. Fix the source page, re-ingest it, `agentkit review`/`approve`.
3. Facts or faithful failures with the right page cited: inspect with `agentkit ask "<question>" --debug`; fix the page wording first, the prompt second.
4. Route or mode failures: adjust guards or routes, then add the case to `dev.md`.
5. Re-run `agentkit eval --set golden` and `--set heldout` live, then `agentkit preflight`. Changing models, prompts, guards or routes invalidates earlier reports by design.

## Incidents
| Symptom | Likely cause | Action |
|---|---|---|
| Banner "search results only" | Model API outage or daily budget reached (`/healthz` llm) | Outage: nothing to do, the breaker retries after `AGENTKIT_LLM_COOLDOWN`. Budget: raise `AGENTKIT_DAILY_BUDGET_USD` or wait for midnight. |
| Answers wrong after an ingest | Bad source or parse | `agentkit snapshots`, then `agentkit rollback`; fix the source; `agentkit review` before approving. |
| Index file corrupt | Disk full or killed write | `agentkit rollback` (keeps the broken file as `<index>.corrupt`). |
| Handoffs show "couldn't reach the librarians' inbox" | LibAnswers or SMTP down | Tickets are saved; staff answer from `/admin`. Fix credentials; `agentkit_handoff_failures_total` shows which backend. |
| Many 429s | Rate limit too low for a class session | Raise `AGENTKIT_RATE` temporarily. |
| Injection or abuse report | Planted text in a document, or a user attack | Find the source with `agentkit ask … --debug`; quarantine it (`remove_origin` via re-ingest without it); add the case to `evals/security/attacks.md`; run `agentkit redteam`. |
| Private data in an answer | Redaction gap | Severity 1: take the service offline, purge logs for that user (`agentkit purge-logs --user`), tell the data protection officer, add a test. |

## Severity and escalation
| Severity | Example | Response |
|---|---|---|
| 1 | private data exposed, service compromised | within 1 hour: systems librarian → IT security → data protection officer |
| 2 | service down, wrong policy answers spreading | same working day: systems librarian → head of reference |
| 3 | one wrong answer, stale page | next working day: content owner fixes the page |

## Content owners
Each source in `knowledge/auc-library/sources.md` names its owner. Defaults until AUC assigns them [VERIFY]:
| Content | Owner |
|---|---|
| Borrowing, renewals, hours | Access services |
| Rare books, archives, finding aids | RBSCL research services |
| Research help, subject librarians | Reference and instruction |
| Knowledge Fountain | Scholarly communication |
| Notices (closures, exam hours) | Library communications, via `/admin` |

## Security review notes
- **SSO:** `AGENTKIT_AUTH=proxy` trusts `X-Forwarded-User`/`-Groups` only when the request also carries `X-Proxy-Secret` equal to `AGENTKIT_PROXY_SECRET`; `deploy/Caddyfile` strips client-sent identity headers and adds the secret. In `docker-compose.yml` the app only `expose`s port 8000 on the internal network, so only Caddy can reach it. In JWT mode, prefer RS256 keys from `AGENTKIT_JWT_JWKS` over a shared HS256 secret. `AGENTKIT_TRUST_PROXY=1` only makes Uvicorn read the client IP from the proxy (for rate limits).
- **WhatsApp webhook:** requests without a valid `X-Hub-Signature-256` are rejected; rotate the app secret if it leaks.
- **Staff page:** admin APIs need the admin role or `X-API-Key`; keep the key out of browsers on shared machines and rotate it each term. Restrict `/admin` and `/metrics` to campus ranges with the commented block in `deploy/Caddyfile`.
- **Access review:** monthly, list who holds the admin role and the admin key; remove leavers. Rotate `AGENTKIT_ADMIN_KEY`, `AGENTKIT_PROXY_SECRET` and the WhatsApp app secret each term or on any suspected leak: set the new value in `.env`, `docker compose up -d`, confirm `/healthz`, revoke the old one. Rotating `AGENTKIT_LOG_KEY` makes older encrypted logs unreadable, so rotate it at a retention boundary.
- **Limits of this review:** these are design checks and automated tests, not a penetration test. An independent test of SSO, the staff page and the WhatsApp webhook is a launch requirement (see PILOT.md).
- **Single process:** the circuit breaker and daily budget live in the server process. The container runs one process; if you run several, the budget applies to each one.
