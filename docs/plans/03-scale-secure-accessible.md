# Plan 3 — Secure the data, scale, and make it accessible

Status: **implemented and tested** (2026-10-02). Every item lists its acceptance test and the measured result.

## Data security
| ID | Item | Implementation | Acceptance test → result |
|---|---|---|---|
| S1 | Sign-in + per-user access | `security.authenticate`: OIDC/JWT (HS256 or RS256/ES256 via JWKS), trusted SSO proxy headers, admin key; `config/access.json` maps groups → access levels; retrieval filters by them | `test_jwt_*`, `test_login_required_and_jwt_access`: a staff token sees a staff-only page, a student token doesn't → ✅ |
| S2 | Data policy, retention, encryption | `SecureLog`: redacted, pseudonymous (HMAC) rows, Fernet-encrypted at rest; 30-day retention and per-user deletion (`agentkit purge-logs [--user]`); [`DATA-POLICY.md`](../DATA-POLICY.md) | `test_secure_log_encrypted_retention_and_user_purge` → ✅ |
| S3 | Minimise data sent to the model | Questions are redacted (emails, Egyptian national IDs, Luhn-valid cards, phones) before any LLM call; ISBNs survive | `test_pii_not_sent_to_llm` → ✅ |
| S4 | Supply chain | `requirements.lock` with hashes (`--require-hashes` in Docker), `pip-audit`, `bandit`, CycloneDX SBOM in CI, Dependabot | pip-audit: **0 known vulnerabilities**; bandit: **0 medium/high** → ✅ |
| S5 | Poisoning defence | https + domain allowlist (`config/allowed-domains.txt`); SHA-256 provenance per source; changed sources held for review (`--review`, `agentkit approve`, staff page) | `test_allowlist_*`, `test_review_holds_changed_sources_until_approved` (both back ends) → ✅ |
| S6 | Safe file handling | Parsing in a forked child with memory cap + timeout; size/page limits; upload extension allowlist and filename sanitising | `test_safe_extract_timeout_and_crash`, `test_file_size_limit`, upload traversal test → ✅ |
| S7 | API protection | Token-bucket rate limits per user/IP, body-size cap, strict CSP + security headers, admin-only staff APIs and `/metrics`; HTTPS via Caddy; MCP stays stdio-only (never exposed on the network) | `test_limits_413_422_and_429`, `test_admin_*` → ✅ |
| S8 | Red-team suite | 30 attacks (EN/AR/Franco-Arabic; direct + planted in documents; OWASP LLM01/02/04/05/06/07/09/10) | `agentkit redteam`: **30/30 blocked** → ✅ |

## Scale
| ID | Item | Implementation | Result |
|---|---|---|---|
| P1 | Storage layer | `BaseIndex` contract; `SqliteIndex` (FTS5 BM25 + trigram, WAL, rarity-pruned queries); optional Qdrant vectors with access filter | SQLite search p95: **97 ms @ 20k**, **232 ms @ 100k** chunks (`scripts/bench_search.py`) |
| P2 | Production server + streaming | FastAPI (`agentkit/api.py`), SSE streaming (`/api/ask/stream`), live streaming with citations from the final message | `test_stream_sse`, `test_live_streaming_parses_citations` → ✅ |
| P3 | Answer cache | LRU + TTL keyed by question + index version + access levels | `test_cache_hits_and_invalidates_on_index_change` → ✅ |
| P4 | Background + incremental ingestion | `JobQueue` worker; unchanged files skipped by hash; changed files replace their chunks; Batch API contextualisation (½ price) | `test_job_queue_*`, `test_incremental_update_*`, `test_batch_contextualize_*` → ✅ |
| P5 | Monitoring | Prometheus `/metrics`: requests by mode, stage latency histograms, cache hits, tokens and USD cost per model; `/admin/api/stats` | `test_metrics_render_and_cost` → ✅ |
| P6 | Docker + load test | Non-root image, healthcheck, read-only container, Caddy TLS, optional Qdrant profile; `scripts/loadtest.py` | Load test (offline LLM, cache off): **218 req/s, p95 105 ms, 0 errors** with 20 concurrent clients |

## Accessibility
| ID | Item | Implementation | Result |
|---|---|---|---|
| A1 | WCAG 2.2 AA web chat | Skip link, landmarks, labelled controls, `role=log` live region with `aria-busy` during streaming, status announcements, focus management, visible focus, 44 px targets, contrast-checked light/dark themes, reduced motion, reflow | axe-core in Chromium: **0 violations** on chat + staff pages, EN and AR (RTL) |
| A2 | Arabic interface | Full UI string table, `lang`/`dir` switching, Arabic README | `test_chat_flow_arabic_and_keyboard` → ✅ |
| A3 | Embeddable widget | `<script src=…/widget.js>`; CORS + `frame-ancestors` only for `AGENTKIT_EMBED_ORIGINS` | `test_embed_origins` → ✅ |
| A4 | WhatsApp + voice | Cloud API webhook with HMAC signature check, rate limits, async replies; voice notes get a typed-reply prompt; browser voice input (Web Speech API, ar-EG/en-US) | `test_whatsapp_webhook` → ✅ |
| A5 | Accessible documents | `agentkit export-accessible`: OCR → HTML with language/direction per paragraph and page landmarks; Arabic PDF glyph fix | `test_accessible_export_*` → ✅ |
| A6 | One-command setup + staff guide | `docker compose up -d`, `.env.example`, staff upload page, [`STAFF-GUIDE.md`](../STAFF-GUIDE.md) | compose config validated; upload flow tested |

## Needs AUC decisions (code is ready, configuration pending) `[VERIFY]`
- Identity provider (OIDC JWKS URL / SAML gateway) and group names for `config/access.json`.
- Hosting, domain and TLS; campus IP ranges for `/admin`.
- Data-processing agreement and zero-data-retention terms with the model provider; PDPL review by AUC legal.
- WhatsApp Business account and Meta app credentials.
