# Agent Kit — instructions for AI coding tools

- Agents live in `plugins/<division>/agents/<name>.md` (Claude Code subagent format); each division folder is a Claude Code plugin listed in `.claude-plugin/marketplace.json`. Create new agents from `templates/agent.template.md` (or the `lean-agent-authoring` skill) and follow `docs/AGENT-DESIGN.md`.
- Domain facts live in `knowledge/`, never hard-coded inside agents. Unverified facts are tagged `[VERIFY]`.
- Before committing, run `scripts/lint-agents.sh` and `pytest -q`; both must pass. Agent changes also need an entry in `evals/agents/smoke.json`. Security-relevant changes: also `agentkit redteam`, `bandit -q -r agentkit -ll`, `pip-audit -r requirements.lock`.
- To build a new agent for any purpose, use the `agent-architect` agent.
- The Python app is in `agentkit/`: `rag.py` (ingestion, provenance, `BaseIndex`), `store_sqlite.py`, `chat.py` (guardrails, routing, cache, streaming), `security.py` (auth, PII, rate limits, encrypted logs, allowlist), `api.py` (FastAPI), `static/` (accessible bilingual UI). Offline it uses a deterministic extractive stand-in; live mode needs `ANTHROPIC_API_KEY`.
- Never log raw questions or identities: use `security.redact` and `security.pseudonym`. Model output is rendered as text only.
- After changing dependencies, regenerate the lockfile: `pip-compile --generate-hashes --strip-extras --extra server --extra mcp -o requirements.lock pyproject.toml`.
