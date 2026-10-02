# Writing Efficient Agents

Adapted from the persona style of [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) (MIT), compressed for **token efficiency** and **routing accuracy**.

## Why shorter
Every agent file is loaded into context on every turn it is active. A 300-line persona costs ~4k tokens per call; the same behaviour fits in 40–80 lines (~600–1,200 tokens). Personality adjectives, "memory" claims, and generic best-practice lists do not change model behaviour — rules, workflows and output formats do.

## The 6-block contract
| Block | Purpose | Budget |
|---|---|---|
| Frontmatter `description` | Router decides from this alone — write *when to use* | 1 sentence |
| Role / Goal | Identity + one measurable outcome | 2 lines |
| Rules | Hard constraints that change behaviour | 3–7 bullets |
| Workflow | Ordered steps | 3–7 steps |
| Output | Exact format with tiny example | ≤ 15 lines |
| Handoffs | When to pass to another agent | 1–4 bullets |

## Checklist
- [ ] `name` is kebab-case and equals the filename
- [ ] `description` starts with "Use when"
- [ ] `tools` is least-privilege (read-only agents get no Write/Bash)
- [ ] ≤ 120 lines (`scripts/lint-agents.sh` enforces)
- [ ] No facts that can go stale inside the agent — put them in `knowledge/` and tell the agent to read/retrieve them
- [ ] Domain facts the agent is unsure of are marked `[VERIFY]`

## Cost levers
- Use `model: haiku` for high-volume, low-judgement agents (FAQ, routing, classification).
- Keep stable text (rules, knowledge) at the *top* of prompts so provider prompt-caching hits.
- Retrieve knowledge on demand (RAG) instead of pasting whole documents.
