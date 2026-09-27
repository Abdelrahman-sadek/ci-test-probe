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

## Fixes (`fixes.patch`)

All six findings are fixed. **Merged upstream in [agent-farmework/agents-framework#6](https://github.com/agent-farmework/agents-framework/pull/6)** (2026-09-27); `fixes.patch` is kept for reference (applies to `d62cbd5` with `git am`).
After the patch: typecheck and lint are clean, **304/304 tests pass** (281 original + 23 new
regression tests), all examples run, and every probe above now comes out safe.

| # | Fix |
| --- | --- |
| 1 | `isPrivateAddress` expands IPv6 to 8 groups and checks the IPv4 embedded in mapped, translated, compatible, NAT64 and 6to4 addresses. It blocks Teredo, site-local, multicast and documentation ranges, adds the missing IPv4 reserved ranges, and refuses addresses it can't parse. |
| 2 | By default, `safeFetch` now uses a `node:http(s)` transport whose `lookup` re-checks each resolved address when the socket connects, so the address that was checked is the one used. The new `EgressPolicy.allowsAddress()` keeps `allowPrivateNetworks` working. A custom `fetchImpl` is still allowed but is documented as unpinned. |
| 3 | New optional `RunStateStore.claim(runId, from, to)` does an atomic status compare-and-set. It is implemented for the in-memory, Postgres (`UPDATE … WHERE status = $2 RETURNING`) and SQLite stores. `resume()` claims the run before running approved tools, and a per-process lock covers stores without `claim()`. A losing concurrent resume fails with "already being resumed". |
| 4 | `commandTool` treats path-like arguments, and the values of allowed `--flag=value` arguments, as workspace paths. Absolute paths, `..` escapes, symlink escapes and `~` are rejected. It can be turned off with `confinePaths: false`. |
| 5 | Injection detection normalizes text first (Unicode normal form, zero-width characters, s p a c e d letters). It adds patterns for reversed word order, rule injection ("new rule:", "from now on you must obey") and es/pt/fr/de/it/ar phrasings. Each signal counts once. False-positive tests were added. |
| 6 | PII detection accepts dotted card numbers, space-separated SSNs (never-issued area numbers excluded) and IBANs in any case, compact or grouped, confirmed by the ISO 13616 mod-97 checksum. |

One existing test changed: the HTTP-tool test used to replace the global `fetch`. The pinned transport
deliberately doesn't use it, so the test now passes the new `defineHttpTool({ fetchImpl })` option.

## Database verification of `claim()` (after merge)

`db-claim-check.ts` runs the merged code against **real PostgreSQL 16.13** (two independent
connection pools) and **SQLite** via `node:sqlite` (two connections to one file). All checks passed
in three consecutive runs:

| Check | PostgreSQL | SQLite |
| --- | --- | --- |
| Claim WAITING→RUNNING succeeds; second claim and missing run fail | PASS | PASS |
| `status` column and the status in the JSON state both updated | PASS | PASS |
| 25 concurrent claims across two connections → exactly 1 winner | PASS | PASS |
| **Two separate runtimes** (like two servers, so the per-process lock can't help) resume the same approval at once → tool runs **once**, the other gets "already being resumed" | PASS | PASS |
| Run ends `COMPLETED` in the database | PASS | PASS |

Both runtimes won the race at least once across the runs, so each side was tested as winner and as loser.

To re-run it: start Postgres on socket `/tmp/pgtest` port 5433, `npm i pg@8` in a folder outside the
repo (set the `createRequire` path in the script), copy the script into `examples/`, then
`npx tsx --tsconfig tsconfig.dev.json examples/db-claim-check.ts`.

## Reproduce

```bash
git clone https://github.com/agent-farmework/agents-framework && cd agents-framework
corepack enable && pnpm install && pnpm check && pnpm examples
cp <this-repo>/agents-framework-eval/probe.test.ts packages/security/src/zz-probe.test.ts
npx vitest run --project security zz-probe   # prints PROBE lines (before the fix)
git am <this-repo>/agents-framework-eval/fixes.patch && pnpm check   # after the fix
```
