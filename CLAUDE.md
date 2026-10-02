# Agent Kit — instructions for AI coding tools

- Agents live in `plugins/<division>/agents/<name>.md` (Claude Code subagent format); each division folder is a Claude Code plugin listed in `.claude-plugin/marketplace.json`. Create new agents from `templates/agent.template.md` (or the `lean-agent-authoring` skill) and follow `docs/AGENT-DESIGN.md`.
- Domain facts live in `knowledge/`, never hard-coded inside agents. Unverified facts are tagged `[VERIFY]`.
- Before committing, run `scripts/lint-agents.sh` and `pytest -q`; both must pass. Agent changes also need an entry in `evals/agents/smoke.json`.
- To build a new agent for any purpose, use the `agent-architect` agent.
- The Python app is in `agentkit/` (OCR → hybrid RAG → guarded AUC Library chat). Offline it uses a deterministic extractive stand-in; live mode needs `ANTHROPIC_API_KEY`.
