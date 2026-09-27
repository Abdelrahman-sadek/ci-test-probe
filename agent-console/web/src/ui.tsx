import { Bot, Brain, CheckCircle2, CircleAlert, CircleDot, Clock, Hand, Library, Loader2, ShieldAlert, Wrench, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { AgentEvent } from "./api";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

const STATUS: Record<string, { label: string; cls: string; icon: ReactNode }> = {
  COMPLETED: { label: "Completed", cls: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300 ring-emerald-500/25", icon: <CheckCircle2 size={13} /> },
  WAITING_FOR_APPROVAL: { label: "Needs approval", cls: "bg-amber-500/14 text-amber-800 dark:text-amber-300 ring-amber-500/30", icon: <Hand size={13} /> },
  RUNNING: { label: "Running", cls: "bg-indigo-500/12 text-indigo-700 dark:text-indigo-300 ring-indigo-500/25", icon: <Loader2 size={13} className="animate-spin" /> },
  CREATED: { label: "Starting", cls: "bg-indigo-500/12 text-indigo-700 dark:text-indigo-300 ring-indigo-500/25", icon: <Loader2 size={13} className="animate-spin" /> },
  FAILED: { label: "Failed", cls: "bg-rose-500/12 text-rose-700 dark:text-rose-300 ring-rose-500/25", icon: <XCircle size={13} /> },
  APPROVAL_EXPIRED: { label: "Expired", cls: "bg-slate-500/12 text-slate-600 dark:text-slate-300 ring-slate-500/25", icon: <Clock size={13} /> },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status.toLowerCase(), cls: "bg-slate-500/12 text-slate-600 ring-slate-500/25", icon: <CircleDot size={13} /> };
  return <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap", s.cls)}>{s.icon}{s.label}</span>;
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("rounded-2xl border border-line bg-panel shadow-[0_1px_2px_rgba(16,20,40,.04)]", className)}>{children}</div>;
}

export function Button({ children, variant = "primary", className, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" | "success" }) {
  const styles = {
    primary: "bg-brand text-brand-ink hover:opacity-90",
    success: "bg-emerald-600 text-white hover:bg-emerald-500",
    danger: "bg-transparent text-rose-600 dark:text-rose-300 ring-1 ring-inset ring-rose-500/40 hover:bg-rose-500/10",
    ghost: "bg-transparent text-ink ring-1 ring-inset ring-line hover:bg-panel-2",
  }[variant];
  return (
    <button
      {...rest}
      className={cx("inline-flex items-center justify-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand", styles, className)}
    >
      {children}
    </button>
  );
}

export const money = (n: number) => (n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);
export const ago = (iso: string) => {
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : new Date(iso).toLocaleDateString();
};
export const outputText = (o: unknown) => (o === null || o === undefined ? "" : typeof o === "string" ? o : JSON.stringify(o, null, 2));

// ------------------------------------------------------------------ timeline

type Kind = "agent" | "model" | "tool" | "approval" | "guard" | "knowledge" | "error";
const KIND: Record<Kind, { icon: ReactNode; dot: string; label: string }> = {
  agent: { icon: <Bot size={14} />, dot: "bg-slate-500", label: "Agent" },
  model: { icon: <Brain size={14} />, dot: "bg-violet-500", label: "Model" },
  tool: { icon: <Wrench size={14} />, dot: "bg-sky-500", label: "Tool" },
  approval: { icon: <Hand size={14} />, dot: "bg-amber-500", label: "Approval" },
  guard: { icon: <ShieldAlert size={14} />, dot: "bg-rose-500", label: "Guardrail" },
  knowledge: { icon: <Library size={14} />, dot: "bg-teal-500", label: "Knowledge" },
  error: { icon: <CircleAlert size={14} />, dot: "bg-rose-600", label: "Error" },
};

/** Turn a raw runtime event into one readable line, or null for low-level noise. */
export function describe(e: AgentEvent): { kind: Kind; text: string } | null {
  const p = e.payload;
  switch (e.type) {
    case "AGENT_STARTED": return { kind: "agent", text: "Run started" };
    case "AGENT_RESUMED": return { kind: "agent", text: `Resumed after ${p.decisions?.map((d: { decision: string }) => d.decision).join(", ")}` };
    case "AGENT_COMPLETED": return { kind: "agent", text: `Finished · ${p.usage?.totalTokens ?? 0} tokens` };
    case "AGENT_FAILED": return { kind: "error", text: `Failed: ${p.error?.message ?? "unknown error"}` };
    case "AGENT_WAITING_FOR_APPROVAL": return { kind: "approval", text: "Paused, waiting for a human decision" };
    case "LLM_CALL_COMPLETED": return { kind: "model", text: `Model replied${p.toolCallCount ? ` and asked for ${p.toolCallCount} tool call${p.toolCallCount > 1 ? "s" : ""}` : ""} · ${p.durationMs} ms` };
    case "LLM_CALL_FAILED": return { kind: "error", text: `Model call failed: ${p.error?.message}` };
    case "TOOL_REQUESTED": return { kind: "tool", text: `Requested ${p.toolName}` };
    case "TOOL_AUTHORIZATION_COMPLETED": return { kind: p.allowed ? "tool" : "error", text: `${p.allowed ? "Allowed" : "Denied"} ${p.toolName} · ${p.reason}` };
    case "TOOL_EXECUTION_COMPLETED": return { kind: "tool", text: `Ran ${p.toolName}${p.cached ? " (cached result)" : ""} · ${p.durationMs} ms` };
    case "TOOL_EXECUTION_FAILED": return { kind: "error", text: `${p.toolName} failed: ${p.error?.message}` };
    case "TOOL_APPROVAL_REQUIRED": return { kind: "approval", text: `${p.toolName} needs approval` };
    case "TOOL_APPROVAL_GRANTED": return { kind: "approval", text: `${p.toolName} approved${p.decidedBy ? ` by ${p.decidedBy}` : ""}${p.modified ? " (edited)" : ""}` };
    case "TOOL_APPROVAL_REJECTED": return { kind: "approval", text: `${p.toolName} rejected${p.reason ? `: ${p.reason}` : ""}` };
    case "GUARDRAIL_TRIGGERED": return { kind: "guard", text: `${p.guardrail} → ${p.action}${p.reason ? ` · ${p.reason}` : ""}` };
    case "RETRIEVAL_COMPLETED": return { kind: "knowledge", text: `Found ${p.resultCount} passage${p.resultCount === 1 ? "" : "s"} in ${p.source}` };
    case "VERIFICATION_COMPLETED": return { kind: "knowledge", text: `Answer check ${p.passed === false ? "failed" : "passed"}` };
    case "LIMIT_EXCEEDED": return { kind: "error", text: `Limit reached: ${p.limitType} (${p.current}/${p.limit})` };
    default: return null;
  }
}

export function Timeline({ events, live }: { events: AgentEvent[]; live?: boolean }) {
  const rows = events.map((e) => ({ e, d: describe(e) })).filter((r): r is { e: AgentEvent; d: NonNullable<ReturnType<typeof describe>> } => r.d !== null);
  const start = events[0] ? Date.parse(events[0].occurredAt) : 0;
  return (
    <ol className="relative ml-2 border-l border-line">
      {rows.map(({ e, d }) => (
        <li key={e.eventId} className="rise relative pl-5 pb-2.5 last:pb-0">
          <span className={cx("absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-panel", KIND[d.kind].dot)} />
          <div className="flex items-start gap-2 text-[13px] leading-5">
            <span className="mt-0.5 text-muted">{KIND[d.kind].icon}</span>
            <span className="flex-1">{d.text}</span>
            <span className="font-mono text-[11px] text-muted tabular-nums">+{Math.max(0, Date.parse(e.occurredAt) - start)}ms</span>
          </div>
        </li>
      ))}
      {live && (
        <li className="relative pl-5 text-[13px] text-muted">
          <span className="absolute -left-[5px] top-1.5 h-2.5 w-2.5 animate-pulse rounded-full bg-indigo-400 ring-4 ring-panel" />
          Working…
        </li>
      )}
    </ol>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-line px-6 py-14 text-center">
      <div className="mb-3 rounded-2xl bg-panel-2 p-3 text-muted">{icon}</div>
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 max-w-sm text-sm text-muted">{children}</div>}
    </div>
  );
}
