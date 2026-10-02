# Agent Kit — instructions for AI coding tools

- Agents live in `agents/<division>/<name>.md` (Claude Code subagent format). Create new ones from `templates/agent.template.md` and follow `docs/AGENT-DESIGN.md`.
- Domain facts live in `knowledge/`, never hard-coded inside agents. Unverified facts are tagged `[VERIFY]`.
- Run `scripts/lint-agents.sh` before committing; it must pass.
- To build a new agent for any purpose, use the `agent-architect` agent.
