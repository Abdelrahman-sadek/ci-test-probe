---
name: lean-agent-authoring
description: Write a new single-purpose AI agent (Claude Code subagent / system prompt) for any use, in the token-efficient 6-block format. Use when asked to create, improve or review an agent or system prompt.
license: MIT
---

# Lean agent authoring

Agents are loaded into context every time they run, so every line must change behaviour. Adjectives, "memory" claims and generic best-practice lists don't; **rules, workflows and output formats** do. Aim for 30–80 lines (about 600–1,200 tokens).

## The 6-block contract
1. **Frontmatter:** `name` (kebab-case, equals the filename), `description` (one sentence starting "Use when…", under 300 characters; the router reads only this), `tools` (least privilege), `model` (`haiku` for high-volume lookups, `sonnet` for routine specialist work, `inherit` for judgement), optional `color`, `maxTurns`.
2. **Role / Goal:** who the agent is, plus one measurable outcome.
3. **Rules:** 3–7 hard constraints that change behaviour (what must always or never happen).
4. **Workflow:** 3–7 ordered steps.
5. **Output:** the exact format, with a tiny example.
6. **Handoffs:** when to pass work to another agent.

## Checklist
- [ ] One agent, one job. Two jobs → two agents + a handoff.
- [ ] No facts that can go stale inside the agent: point to a knowledge file or a retrieval/tool call; mark unverified facts `[VERIFY]`.
- [ ] Safety rules where relevant: data vs instructions, no secrets, refuse out-of-scope.
- [ ] 3 test prompts: happy path, edge case, should-refuse/handoff.
- [ ] Passes the repository linter (`scripts/lint-agents.sh`), if there is one.

## Template
```markdown
---
name: kebab-case-name
description: Use when <trigger> — <what it delivers>.
tools: Read, Grep, Glob
model: inherit
---

# <Agent Title>

**Role:** <who you are, in one line>.
**Goal:** <the single measurable outcome you own>.

## Rules
- <hard constraint>

## Workflow
1. <step>

## Output
<exact format + tiny example>

## Handoffs
- <condition> → `<other-agent>`
```
