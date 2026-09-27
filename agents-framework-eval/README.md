# Evaluation: agent-farmework/agents-framework

Evaluated upstream commit `d62cbd5` (2026-09-27) on Node 22, pnpm 10.

## What it is

A TypeScript/Node.js framework (Apache-2.0, v0.1.0 pre-release) for building production AI agents.
Its central design idea: **the model only *requests* actions; deterministic code decides** whether
they are allowed, runs them under limits, and records them. Permissions, budgets, retries,
approvals, and the audit log never depend on model output.

It's a pnpm monorepo of 15 packages (~9.5k lines of source code):

| Package | LOC | Tests | Role |
| --- | ---: | ---: | --- |
| `core` | 2920 | 66 | Agent loop, runtime, events, limits, run-state store, approvals/resume, reflection, skills, scripted test provider |
| `tools` | 931 | 69 | `defineTool` (Zod in/out), `ToolRuntime` pipeline: validate → authorize → approve → execute (timeout, retry, rate limit, idempotency) → audit |
| `production` | 886 | 15 | `AgentService`/`AgentWorker`, PostgreSQL/Redis/SQLite store adapters |
| `orchestration` | 656 | 20 | `defineOrchestrator`, supervisor, parallel workers |
| `observability` | 524 | 7 | OpenTelemetry sink, cost tracker, dashboard |
| `security` | 490 | 22 | Guardrails (PII, injection, secrets), RBAC/ABAC/tenant policies, SSRF egress, `defineHttpTool` |
| `knowledge` | 474 | 12 | RAG knowledge base with citations |
| `evaluation` | 471 | 8 | `defineEvaluation`, evaluators, regression checks |
| `llm` | 451 | 12 | Model registry, gateway, cost/capability router, OpenAI-compatible provider |
| `memory` | 432 | 13 | User/conversation memory |
| `cli` | 379 | 8 | `agent create / dev / trace / dashboard` |
| `sandbox` | 247 | 13 | Workspace file tools confined to a root directory, allow-listed `commandTool` with no shell |
| `context` | 198 | 8 | Context-window budgeting engine |
| `provider-anthropic` | 178 | 6 | Claude provider |
| `mcp` | 107 | 2 | Use MCP server tools as framework tools |

### How a run works

```
agent.run({input, user})
  └─ loop (bounded by maxSteps / maxToolCalls / maxCost / timeout)
       ├─ LLM call via provider (Anthropic, OpenAI-compatible, scripted)
       ├─ input/output/tool_result guardrails
       └─ for each tool call → ToolRuntime:
            Zod validate → permission policy → approval?
              ├─ needs approval → state persisted, status WAITING_FOR_APPROVAL
              │     └─ agent.resume({runId, approvals}) continues later
              └─ execute (timeout/retry/idempotency) → audit record → event
```

Every step emits typed events (`AGENT_STARTED`, `TOOL_AUTHORIZATION_COMPLETED`, …), which makes runs
fully traceable.

## Test results

| Check | Result |
| --- | --- |
| `pnpm install` | OK (3.3s) |
| `pnpm typecheck` (strict TS) | Pass |
| `pnpm lint` | Pass, 0 warnings |
| `pnpm test` | **281/281 tests passed**, 26 files |
| 6 examples (hello, approval, rag, research, orchestrator, enterprise) | All run offline, exit 0, produce the documented output |

## Adversarial probes (my own tests, `probe.test.ts`)

I wrote extra tests aimed at the framework's safety claims, beyond its own suite.

| # | Area | Finding | Severity |
| --- | --- | --- | --- |
| 1 | **SSRF / egress** | `isPrivateAddress` misses the hex form of IPv4-mapped IPv6. Node's `URL` normalizes `[::ffff:127.0.0.1]` to `[::ffff:7f00:1]`, which is classified as *public*. It also misses NAT64 (`64:ff9b::/96`), 6to4 (`2002::/16`), deprecated site-local `fec0::/10`, and `198.18.0.0/15`. The host allow-list still has to match, so this is exploitable only when an allowed host is a literal IP or resolves to one of these ranges. | Medium |
| 2 | **SSRF / egress** | `safeFetch` checks DNS in `policy.check()`, but `fetch()` resolves again, so a DNS-rebinding window exists (time-of-check to time-of-use). The connection is not pinned to the checked IP. | Medium |
| 3 | **Approvals** | Two concurrent `resume()` calls with the same approval **both complete and run the tool twice** (the probe counted 2 executions). The run state is loaded and checked without a lock or compare-and-swap. Tools that set `idempotency` are protected; tools without it are not. | High for payments-style tools |
| 4 | **Sandbox** | `commandTool` rejects unlisted flags but does **not** confine path arguments: `cat /etc/passwd` succeeds with `allow: { cat: [] }`. Approval is on by default, which reduces the risk, but the docs imply the command is scoped to the workspace. | Medium |
| 5 | **Prompt-injection guardrail** | Regex heuristics. It catches the classic phrasing, but scores **0** for "Ignore the instructions above.", spaced-out letters, non-English text (tested Spanish and Arabic), and "new rule: obey me". The code itself documents it as defense in depth, not a control. | Low (by design) |
| 6 | **PII guardrail** | Misses dotted card numbers (`4111.1111.1111.1111`), space-separated SSNs, and lowercase or space-grouped IBANs. | Low |
| — | Limits | A model that loops tool calls is stopped at `maxToolCalls` with `LIMIT_EXCEEDED`. Correct. | OK |
| — | Modified approvals | A human "modified" decision with invalid arguments is re-validated, and the tool does not run. Correct. | OK |
| — | Egress allow-list | Suffix tricks (`evilexample.com`, `api.example.com.evil.io`) are rejected. Correct. | OK |

Also worth knowing: `resume()` does not check *who* approves. `decidedBy` is a free-text string, so
authorizing the approver is left to the application.

## Verdict: the strongest parts

1. **`tools` + `core`: the tool runtime and agent loop.** This is the framework's real value.
   Tool calls go through validation, authorization, approval, and audit, with hard limits, typed
   events, and pause/resume. It has the most tests (135), and every limit I probed held.
2. **`core/testing` (scripted provider).** Agents can be tested deterministically offline. This is
   rare in agent frameworks and makes CI practical.
3. **`sandbox` workspace tools.** Symlink-, traversal-, and null-byte-safe path confinement. It is
   well built (command-argument confinement aside, see #4).
4. **`orchestration` and `production`.** Useful building blocks for multi-agent work and durable
   service/worker deployments.

Weaker or thinner:

- `mcp` has only 2 tests.
- The `security` guardrails are regex-based.
- There is no real-provider integration test. `provider-anthropic` is unit-tested with mocks only.

**Overall:** a clean, strictly typed, well-tested 0.x framework with a sound architecture. It's good
to adopt for its tool runtime, approvals, and limits. Before production, fix or work around #1–#4:
set `idempotency` on every side-effecting tool, route egress through a proxy that pins the resolved
IP, and constrain `commandTool` arguments.

## Reproduce

```bash
git clone https://github.com/agent-farmework/agents-framework && cd agents-framework
corepack enable && pnpm install && pnpm check && pnpm examples
cp <this-repo>/agents-framework-eval/probe.test.ts packages/security/src/zz-probe.test.ts
npx vitest run --project security zz-probe   # prints PROBE lines
```
