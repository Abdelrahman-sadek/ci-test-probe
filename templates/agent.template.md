---
name: kebab-case-name            # unique, matches filename
description: One sentence — WHEN to use this agent (the router reads only this line). Start with "Use when…".
tools: Read, Grep, Glob          # least privilege; omit to inherit all
model: inherit                   # or sonnet / haiku for cheap high-volume agents
---

# <Agent Title>

**Role:** <who you are, in one line>.
**Goal:** <the single measurable outcome you own>.

## Rules
- <hard constraint — things that must never/always happen>
- <keep 3–7 rules; each must change behaviour, no platitudes>

## Workflow
1. <step>
2. <step>
3. <step>

## Output
<exact format: headings, JSON schema, or table. Show a tiny example.>

## Handoffs
- <condition> → `<other-agent>`
