import { createHash, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import type { AgentEvent, AgentState } from "@agent-farmework/core";
import { Hono } from "hono";
import { deleteCookie, getCookie, setCookie } from "hono/cookie";
import { secureHeaders } from "hono/secure-headers";
import { streamSSE } from "hono/streaming";
import { z } from "zod";
import { createAgents, operator } from "./agents.js";
import type { Database } from "./db.js";

export interface AppOptions {
  db: Database;
  adminPassword: string;
  secureCookies: boolean;
}

const SESSION_COOKIE = "ac_session";
const SESSION_TTL_MS = 12 * 3_600_000;
const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "APPROVAL_EXPIRED", "WAITING_FOR_APPROVAL"]);

const sha256 = (s: string): Buffer => createHash("sha256").update(s).digest();

function parseArgs(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

/** What the UI needs about a run, without internal message history. */
export function summarize(state: AgentState) {
  const firstUser = state.messages.find((m) => m.role === "user")?.content;
  return {
    runId: state.runId,
    agentId: state.agentId,
    status: state.status,
    input: typeof firstUser === "string" ? firstUser : typeof state.input === "string" ? state.input : JSON.stringify(state.input),
    output: state.output ?? null,
    error: state.error === undefined ? null : { code: state.error.code, message: state.error.message },
    usage: state.usage,
    createdAt: state.createdAt,
    updatedAt: state.updatedAt,
    sources: state.contextItems.map((i) => ({ title: i.source?.title ?? i.id, score: i.score ?? null })),
    pendingApprovals: state.pendingApprovals.map((p) => ({
      approvalId: p.approval.approvalId,
      toolName: p.approval.toolName,
      reason: p.approval.reason ?? null,
      requestedAt: p.approval.requestedAt,
      expiresAt: p.approval.expiresAt ?? null,
      arguments: parseArgs(p.toolCall.arguments),
    })),
  };
}

export async function createApp(options: AppOptions) {
  const { db } = options;
  const { agents, info } = await createAgents(db);
  const sessions = new Map<string, number>();
  const loginAttempts = new Map<string, { count: number; resetAt: number }>();
  const expected = sha256(options.adminPassword);

  const app = new Hono();
  app.use("*", secureHeaders());

  app.get("/api/health", (c) => c.json({ ok: true, uptimeSeconds: Math.round(process.uptime()) }));

  // ------------------------------------------------------------ auth
  app.post("/api/login", async (c) => {
    const ip = c.req.header("x-forwarded-for")?.split(",")[0]?.trim() ?? "local";
    const now = Date.now();
    const attempts = loginAttempts.get(ip);
    if (attempts !== undefined && attempts.resetAt > now && attempts.count >= 5) return c.json({ error: "Too many attempts. Try again in a minute." }, 429);
    const body = z.object({ password: z.string().max(200) }).safeParse(await c.req.json().catch(() => ({})));
    const ok = body.success && timingSafeEqual(sha256(body.data.password), expected);
    if (!ok) {
      loginAttempts.set(ip, attempts !== undefined && attempts.resetAt > now ? { ...attempts, count: attempts.count + 1 } : { count: 1, resetAt: now + 60_000 });
      return c.json({ error: "Wrong password." }, 401);
    }
    loginAttempts.delete(ip);
    const token = randomBytes(32).toString("base64url");
    sessions.set(token, now + SESSION_TTL_MS);
    setCookie(c, SESSION_COOKIE, token, { httpOnly: true, sameSite: "Strict", secure: options.secureCookies, path: "/", maxAge: SESSION_TTL_MS / 1000 });
    return c.json({ ok: true });
  });

  app.post("/api/logout", (c) => {
    const token = getCookie(c, SESSION_COOKIE);
    if (token !== undefined) sessions.delete(token);
    deleteCookie(c, SESSION_COOKIE, { path: "/" });
    return c.json({ ok: true });
  });

  app.use("/api/*", async (c, next) => {
    if (c.req.path === "/api/login" || c.req.path === "/api/health") return next();
    const token = getCookie(c, SESSION_COOKIE);
    const expires = token === undefined ? undefined : sessions.get(token);
    if (expires === undefined || expires < Date.now()) return c.json({ error: "Not signed in." }, 401);
    return next();
  });

  app.get("/api/me", (c) => c.json({ userId: operator.userId, tenantId: operator.tenantId }));

  // ------------------------------------------------------------ agents & runs
  app.get("/api/agents", (c) => c.json(info));
  app.get("/api/settings", (c) => c.json({ model: "Scripted demo model (offline, no API key)", database: "SQLite", operator }));

  app.post("/api/agents/:agentId/runs", async (c) => {
    const agent = agents.get(c.req.param("agentId"));
    if (agent === undefined) return c.json({ error: "Unknown agent." }, 404);
    const body = z.object({ input: z.string().trim().min(1).max(2_000) }).safeParse(await c.req.json().catch(() => ({})));
    if (!body.success) return c.json({ error: "Message must be 1–2000 characters." }, 400);
    const runId = `run_${randomUUID()}`;
    // Runs continue in the background; the UI follows them over the event stream.
    void agent.run({ input: body.data.input, user: operator, runId }).catch((error: unknown) => console.error(`run ${runId} crashed`, error));
    return c.json({ runId }, 202);
  });

  app.get("/api/runs", (c) => c.json(db.queries.list().map(summarize)));
  app.get("/api/approvals", (c) => c.json(db.queries.waiting().map(summarize)));

  app.get("/api/runs/:runId", async (c) => {
    const state = await db.runs.load(c.req.param("runId"));
    if (state === undefined) return c.json({ error: "Run not found." }, 404);
    return c.json({ ...summarize(state), events: db.events.forRun(state.runId), audit: db.audit.forRun(state.runId) });
  });

  app.get("/api/runs/:runId/stream", (c) => {
    const runId = c.req.param("runId");
    const after = Number(c.req.query("after") ?? 0);
    return streamSSE(c, async (stream) => {
      let last = Number.isFinite(after) ? after : 0;
      let done = false;
      const queue: AgentEvent[] = [];
      let wake: (() => void) | undefined;
      const unsubscribe = db.events.subscribe(runId, (event) => {
        queue.push(event);
        wake?.();
      });
      stream.onAbort(() => {
        done = true;
        wake?.();
      });
      queue.unshift(...db.events.forRun(runId, last)); // replay what happened before we subscribed
      const keepAlive = setInterval(() => void stream.writeSSE({ event: "ping", data: "" }), 15_000);
      try {
        while (!done) {
          const event = queue.shift();
          if (event === undefined) {
            await new Promise<void>((resolve) => {
              wake = resolve;
            });
            wake = undefined;
            continue;
          }
          if (event.sequence <= last) continue;
          last = event.sequence;
          await stream.writeSSE({ event: "agent-event", id: String(event.sequence), data: JSON.stringify(event) });
          if (event.type.startsWith("AGENT_") && TERMINAL.has(event.type.replace("AGENT_", ""))) {
            const state = await db.runs.load(runId);
            if (state !== undefined && TERMINAL.has(state.status)) await stream.writeSSE({ event: "run-state", data: JSON.stringify(summarize(state)) });
          }
        }
      } finally {
        clearInterval(keepAlive);
        unsubscribe();
      }
    });
  });

  // ------------------------------------------------------------ approvals
  app.post("/api/runs/:runId/approvals", async (c) => {
    const runId = c.req.param("runId");
    const body = z
      .object({
        approvalId: z.string().min(1),
        decision: z.enum(["approved", "rejected", "modified"]),
        modifiedArguments: z.record(z.string(), z.unknown()).optional(),
        reason: z.string().max(500).optional(),
      })
      .safeParse(await c.req.json().catch(() => ({})));
    if (!body.success) return c.json({ error: "Invalid decision." }, 400);
    const state = await db.runs.load(runId);
    const agent = state === undefined ? undefined : agents.get(state.agentId);
    if (state === undefined || agent === undefined) return c.json({ error: "Run not found." }, 404);
    try {
      const result = await agent.resume({
        runId,
        approvals: [
          {
            approvalId: body.data.approvalId,
            decision: body.data.decision,
            decidedBy: operator.userId,
            ...(body.data.modifiedArguments === undefined ? {} : { modifiedArguments: body.data.modifiedArguments }),
            ...(body.data.reason === undefined ? {} : { reason: body.data.reason }),
          },
        ],
      });
      const after = await db.runs.load(result.runId);
      return c.json(after === undefined ? { status: result.status } : summarize(after));
    } catch (error) {
      return c.json({ error: error instanceof Error ? error.message : "Could not apply the decision." }, 409);
    }
  });

  return app;
}
