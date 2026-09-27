import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { createRuntime, defineAgent, type RunStateStore } from "@agent-farmework/core";
import { createScriptedProvider } from "@agent-farmework/core/testing";
import { models } from "@agent-farmework/llm";
import { PostgresRunStateStore, SqliteRunStateStore, openSqlite } from "@agent-farmework/production";
import { ToolRuntime, defineTool } from "@agent-farmework/tools";
import { z } from "zod";

const require = createRequire("/tmp/claude-0/-home-user-ci-test-probe/76e94463-f8ac-535f-a3ac-83e3bfc5ef85/scratchpad/dbtest/");
const { Pool } = require("pg") as { Pool: new (o: object) => { query: (...a: unknown[]) => Promise<{ rows: unknown[] }>; end(): Promise<void> } };

let failures = 0;
const check = (name: string, ok: boolean, detail: unknown = "") => { if (!ok) failures++; console.log(`  ${ok ? "PASS" : "FAIL"} ${name} ${detail === "" ? "" : JSON.stringify(detail)}`); };

async function suite(label: string, storeA: RunStateStore, storeB: RunStateStore, rawStatus: (runId: string) => Promise<{ col: string; json: string }>) {
  console.log(`\n== ${label}`);
  const base = { agentId: "a", status: "WAITING_FOR_APPROVAL", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
  // 1. compare-and-set semantics
  await storeA.save({ ...base, runId: "cas-1" } as never);
  check("claim WAITING→RUNNING succeeds", (await storeA.claim!("cas-1", "WAITING_FOR_APPROVAL", "RUNNING")) === true);
  check("second claim fails", (await storeA.claim!("cas-1", "WAITING_FOR_APPROVAL", "RUNNING")) === false);
  check("missing run fails", (await storeA.claim!("nope", "WAITING_FOR_APPROVAL", "RUNNING")) === false);
  const raw = await rawStatus("cas-1");
  check("status column and JSON state both updated", raw.col === "RUNNING" && raw.json === "RUNNING", raw);
  check("load() sees RUNNING", (await storeB.load("cas-1"))?.status === "RUNNING");
  // 2. race: 25 concurrent claims split across two connections
  await storeA.save({ ...base, runId: "race-1" } as never);
  const wins = (await Promise.all(Array.from({ length: 25 }, (_, i) => (i % 2 ? storeA : storeB).claim!("race-1", "WAITING_FOR_APPROVAL", "RUNNING")))).filter(Boolean).length;
  check("25 concurrent claims → exactly 1 winner", wins === 1, { wins });
  // 3. end to end: two runtimes (= two processes) resume the same approval concurrently
  let executions = 0;
  const refund = defineTool({ name: "refund", description: "Refund", input: z.object({ amount: z.number() }), approval: { required: true }, execute: async ({ amount }) => { executions++; await new Promise((r) => setTimeout(r, 50)); return { amount }; } });
  const provider = createScriptedProvider([{ toolCalls: [{ id: "c1", name: "refund", arguments: { amount: 5 } }] }, { text: "done" }], { id: "openai" });
  const mk = (store: RunStateStore) => defineAgent({ name: "support", model: models.openai("m"), instructions: "", tools: [refund], runtime: createRuntime({ providers: [provider], tools: new ToolRuntime(), stateStore: store }) });
  const agentA = mk(storeA), agentB = mk(storeB);
  const paused = await agentA.run({ input: "refund" });
  const approvals = [{ approvalId: paused.pendingApprovals[0]!.approvalId, decision: "approved" as const }];
  const results = await Promise.allSettled([agentA.resume({ runId: paused.runId, approvals }), agentB.resume({ runId: paused.runId, approvals })]);
  const outcome = results.map((r) => (r.status === "fulfilled" ? r.value.status : (r.reason as Error).message.replace(/run '.*?'/, "run")));
  check("two runtimes resuming concurrently → tool runs once", executions === 1, { executions, outcome });
  check("run ends COMPLETED in the database", (await storeB.load(paused.runId))?.status === "COMPLETED");
}

// PostgreSQL 16 (two pools = independent connections)
const pgA = new Pool({ host: "/tmp/pgtest", port: 5433, user: "postgres", database: "postgres", max: 10 });
const pgB = new Pool({ host: "/tmp/pgtest", port: 5433, user: "postgres", database: "postgres", max: 10 });
const pgStoreA = new PostgresRunStateStore(pgA as never), pgStoreB = new PostgresRunStateStore(pgB as never);
await pgA.query("DROP TABLE IF EXISTS agent_runs");
await pgStoreA.migrate();
await suite("PostgreSQL", pgStoreA, pgStoreB, async (id) => {
  const { rows } = (await pgA.query("SELECT status AS col, state->>'status' AS json FROM agent_runs WHERE run_id = $1", [id])) as { rows: { col: string; json: string }[] };
  return rows[0]!;
});
await pgA.end(); await pgB.end();

// SQLite (two connections to the same file)
const file = `${mkdtempSync(`${tmpdir()}/sq-`)}/runs.db`;
const dbA = await openSqlite(file), dbB = await openSqlite(file);
await suite("SQLite", new SqliteRunStateStore(dbA), new SqliteRunStateStore(dbB), async (id) =>
  dbA.prepare("SELECT status AS col, json_extract(state, '$.status') AS json FROM agent_runs WHERE run_id = ?").get(id) as { col: string; json: string });

console.log(`\n${failures === 0 ? "ALL PASSED" : `${failures} FAILED`}`);
process.exit(failures === 0 ? 0 : 1);
