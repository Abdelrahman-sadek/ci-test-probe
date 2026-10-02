# Deployment guide

## One command
```bash
cp .env.example .env        # fill DOMAIN, keys (see comments)
docker compose up -d        # app + Caddy (automatic HTTPS) — first start builds the seed index
docker compose --profile dense up -d   # optional Qdrant for dense vectors
```
Without Docker: `pip install -e ".[server,mcp]" && agentkit --index data/index.db ingest knowledge/auc-library/pages && agentkit --index data/index.db serve --host 0.0.0.0`.

## Sign-in (pick one) `[VERIFY AUC identity provider]`
| Mode | Set | When |
|---|---|---|
| OIDC bearer tokens | `AGENTKIT_AUTH=jwt`, `AGENTKIT_JWT_JWKS=https://<idp>/.well-known/jwks.json`, `AGENTKIT_JWT_AUDIENCE`, `AGENTKIT_JWT_ISSUER` | Microsoft Entra ID, Google, Keycloak… |
| SSO reverse proxy | `AGENTKIT_AUTH=proxy`, put oauth2-proxy or a Shibboleth SP in front; it sets `X-Forwarded-User/-Groups` | SAML campuses |
| Public demo | `AGENTKIT_AUTH=none` | Public pages only |
Add `AGENTKIT_REQUIRE_LOGIN=1` to block anonymous use. Map directory groups to access levels in `config/access.json`. Admins: `AGENTKIT_ADMIN_GROUPS` or `AGENTKIT_ADMIN_KEY`.

## Scale
- **Index:** use a `.db` path (SQLite FTS5) for anything beyond a few thousand chunks. Measured p95 search latency: 97 ms at 20k chunks, 232 ms at 100k chunks.
- **Workers:** one container serves ~200 requests/s offline. With live answers, generation time dominates, so scale containers horizontally behind Caddy (the SQLite file is read-mostly; for several writers use a shared volume plus one ingestion worker).
- **Cache:** on by default (`AGENTKIT_CACHE_SIZE`, `AGENTKIT_CACHE_TTL`); repeated questions skip retrieval and the model.
- **Cost:** contextualise with `agentkit ingest --contextualize batch` (Batch API, half price). Watch `agentkit_llm_cost_usd_total` on `/metrics`.

## Monitoring
Scrape `/metrics` (Prometheus; requires admin unless `AGENTKIT_METRICS_PUBLIC=1`). Useful alerts: handoff share > 30%, p95 `agentkit_stage_seconds{stage="total"}` > 5 s, `agentkit_rate_limited_total` spikes, and daily cost.

## Channels
- **Website widget:** add `<script src="https://<assistant>/widget.js" defer></script>` to library pages and list their origin in `AGENTKIT_EMBED_ORIGINS`.
- **WhatsApp:** create a Meta app, set the webhook to `https://<assistant>/whatsapp`, and set `AGENTKIT_WA_VERIFY_TOKEN`, `AGENTKIT_WA_APP_SECRET`, `AGENTKIT_WA_TOKEN` and `AGENTKIT_WA_PHONE_ID`.
- **MCP (Claude Desktop/Code, Cursor):** `claude mcp add agentkit -- agentkit --index /abs/data/index.db mcp`. Stdio only, never exposed on the network.

## Release checklist
`scripts/lint-agents.sh && pytest -q && agentkit eval --min-pass 0.95 --min-recall 0.9 && agentkit redteam && pip-audit -r requirements.lock && bandit -q -r agentkit -ll`
