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
- [ ] File lives in `plugins/<division>/agents/` and `name` is kebab-case, equal to the filename
- [ ] `description` starts with "Use when"
- [ ] `tools` is least-privilege (read-only agents get no Write/Bash)
- [ ] `model` tier chosen on purpose (`haiku` high-volume, `sonnet` routine, `inherit` judgement); description ≤ 300 chars
- [ ] ≤ 120 lines and a smoke test in `evals/agents/smoke.json` (`scripts/lint-agents.sh` enforces both)
- [ ] No facts that can go stale inside the agent — put them in `knowledge/` and tell the agent to read/retrieve them
- [ ] Domain facts the agent is unsure of are marked `[VERIFY]`
- [ ] No filler words (seamless, robust, delve…) and no "I hope this helps" closers in instructions or output; the linter rejects them (rules adapted from [antislop](https://github.com/miqdadbadjuber/anti-slop))

## Measured cost
`claude plugin details` reports the always-on cost of each division plugin: core ~347 tokens, rag ~325, chat ~209, auc-library ~424. That's the whole roster's routing cost per session.

## Cost levers
- Use `model: haiku` for high-volume, low-judgement agents (FAQ, routing, classification).
- Keep stable text (rules, knowledge) at the *top* of prompts so provider prompt-caching hits.
- Retrieve knowledge on demand (RAG) instead of pasting whole documents.
