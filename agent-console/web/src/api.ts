export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  examples: string[];
  tools: { name: string; description: string; approval?: string }[];
  guardrails: string[];
  limits: { maxSteps: number; maxToolCalls: number };
}

export interface PendingApproval {
  approvalId: string;
  toolName: string;
  reason: string | null;
  requestedAt: string;
  expiresAt: string | null;
  arguments: Record<string, unknown>;
}

export interface RunSummary {
  runId: string;
  agentId: string;
  status: string;
  input: string;
  output: unknown;
  error: { code: string; message: string } | null;
  usage: { totalTokens: number; estimatedCostUsd: number; llmCalls: number; toolCalls: number };
  createdAt: string;
  updatedAt: string;
  sources: { title: string; score: number | null }[];
  pendingApprovals: PendingApproval[];
}

export interface AgentEvent {
  eventId: string;
  sequence: number;
  runId: string;
  type: string;
  occurredAt: string;
  payload: Record<string, any>;
}

export interface AuditRecord {
  auditId: string;
  recordedAt: string;
  toolName: string;
  outcome: string;
  input?: unknown;
  durationMs: number;
  authorization?: { allowed: boolean; policy: string; reason: string };
  approval?: { decision: string; decidedBy?: string };
}

export interface RunDetail extends RunSummary {
  events: AgentEvent[];
  audit: AuditRecord[];
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, { ...init, headers: { "content-type": "application/json", ...(init.headers ?? {}) }, credentials: "same-origin" });
  const body = (await res.json().catch(() => ({}))) as { error?: string };
  if (!res.ok) {
    if (res.status === 401 && path !== "/api/login") window.dispatchEvent(new Event("signed-out"));
    throw new ApiError(res.status, body.error ?? `Request failed (${res.status})`);
  }
  return body as T;
}

export const post = <T>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });

/** Follow a run live. Calls `onEvent` for each step and `onState` when the run settles. */
export function followRun(runId: string, onEvent: (e: AgentEvent) => void, onState: (s: RunSummary) => void): () => void {
  const source = new EventSource(`/api/runs/${runId}/stream`);
  source.addEventListener("agent-event", (m) => onEvent(JSON.parse((m as MessageEvent<string>).data) as AgentEvent));
  source.addEventListener("run-state", (m) => {
    onState(JSON.parse((m as MessageEvent<string>).data) as RunSummary);
    source.close();
  });
  return () => source.close();
}
