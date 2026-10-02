# 🤖 Agent Kit — lean AI agents for any use, with RAG, Chat & AUC Library divisions

A small, efficient set of Markdown agent definitions you can drop into **Claude Code, Cursor, Codex, Copilot, Gemini CLI, Aider** or use as system prompts in your own app.

Inspired by [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents), then cut down hard: **~30 lines per agent instead of ~200–300**. You get the same behaviour from far fewer tokens, and routing is more accurate. See [`docs/AGENT-DESIGN.md`](docs/AGENT-DESIGN.md).

## Roster

| Division | Agent | Use when… |
|---|---|---|
| 🧩 Core | `agent-architect` | you need a **new agent for any purpose** |
| | `orchestrator` | a task needs several agents |
| | `prompt-engineer` | a prompt is weak, inconsistent, or costs too much |
| | `evaluator` | you need to prove it works (golden tests) |
| 🔎 RAG | `rag-architect` | designing a RAG system end to end |
| | `rag-ingestion-engineer` | parsing, chunking, metadata, refresh |
| | `rag-retrieval-engineer` | retrieval returns wrong or missing chunks |
| | `rag-evaluator` | measuring recall, faithfulness, citations |
| 💬 Chat | `chatbot-architect` | building a chat assistant |
| | `chat-guardrails` | injection, PII, scope, and escalation hardening |
| | `arabic-english-localizer` | Arabic (MSA/Egyptian/Arabizi) + English users |
| 🏛️ AUC Library | `auc-library-concierge` | front-door chat: hours, borrowing, access, services |
| | `auc-research-assistant` | finding sources, databases, search strings, citations |
| | `auc-catalog-navigator` | finding a specific item, call number, availability |
| | `auc-special-collections-guide` | rare books, archives, manuscripts, photos |
| | `auc-library-rag-builder` | building the knowledge base behind the AUC agents |

## Quick start

```bash
scripts/install.sh claude                 # all agents → ~/.claude/agents
scripts/install.sh claude --project rag   # only RAG agents → ./.claude/agents
scripts/install.sh cursor chat auc-library  # → ./.cursor/rules/*.mdc
scripts/install.sh agents-md              # → AGENTS.generated.md (Codex, Copilot, Gemini CLI, Aider…)
```
Then just ask: *"Use agent-architect to make me an agent that triages support emails."*

**In your own app:** load an agent file (minus the frontmatter) as the system prompt, and add retrieval and tools (see the RAG agents).

## AUC Library chatbot: path to production

```
User (AR/EN/Arabizi) → auc-library-concierge ──┬─ auc-research-assistant
                                               ├─ auc-catalog-navigator   (live catalog tool)
                                               └─ auc-special-collections-guide
                 grounded by → RAG index of official AUC Library pages (auc-library-rag-builder)
```
1. **Verify facts.** Fill every `[VERIFY]` in [`knowledge/auc-library/facts.md`](knowledge/auc-library/facts.md) from official AUC Libraries pages. The agents are written so they will not state unverified facts as certain.
2. **Get permission** from AUC Libraries to crawl the site and use catalog/repository APIs. Then list the sources in [`sources.md`](knowledge/auc-library/sources.md).
3. **Build the index** with `auc-library-rag-builder`.
4. **Fill in and run** [`evals/auc-library/golden-questions.md`](evals/auc-library/golden-questions.md) with `rag-evaluator`. Target ≥ 90% faithfulness.
5. **Harden** the bot with `chat-guardrails` and `arabic-english-localizer` before launch.

> This is an independent project. It is not affiliated with or endorsed by The American University in Cairo.

## Repo layout
```
agents/<division>/*.md    agent definitions (Claude Code subagent format)
templates/                blank agent template
docs/AGENT-DESIGN.md      how to write efficient agents
knowledge/                domain facts (kept out of the agents so they stay current)
evals/                    golden test sets
scripts/                  install.sh, lint-agents.sh (CI-enforced)
```

## Add an agent
Copy `templates/agent.template.md` (or ask `agent-architect`), then run `scripts/lint-agents.sh`.

## License
MIT. The persona format is adapted from agency-agents (MIT, © msitarzewski).
