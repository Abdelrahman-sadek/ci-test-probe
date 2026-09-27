import { Check, Clock, Hand, Inbox, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, post, type AgentInfo, type PendingApproval, type RunSummary } from "../api";
import { Button, Card, Empty, ago } from "../ui";

const label = (k: string) => k.replace(/([A-Z])/g, " $1").replace(/^./, (c) => c.toUpperCase());

export function ApprovalCard({ run, approval, agentName, onDecided }: { run: RunSummary; approval: PendingApproval; agentName: string; onDecided: (after: RunSummary) => void }) {
  const [mode, setMode] = useState<"idle" | "reject" | "edit">("idle");
  const [reason, setReason] = useState("");
  const [edited, setEdited] = useState<Record<string, unknown>>(approval.arguments);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const decide = async (decision: "approved" | "rejected" | "modified") => {
    setBusy(true);
    setError(null);
    try {
      const after = await post<RunSummary>(`/api/runs/${run.runId}/approvals`, {
        approvalId: approval.approvalId,
        decision,
        ...(decision === "rejected" && reason.trim() ? { reason: reason.trim() } : {}),
        ...(decision === "modified" ? { modifiedArguments: edited } : {}),
      });
      onDecided(after);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not apply the decision.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rise rounded-2xl border border-amber-500/40 bg-amber-500/[.06] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-xl bg-amber-500 text-white"><Hand size={16} /></span>
          <div>
            <p className="text-sm font-semibold">{agentName} wants to run <code className="rounded bg-panel-2 px-1 text-[12px]">{approval.toolName}</code></p>
            <p className="text-xs text-muted">{approval.reason ?? "Approval required"}</p>
          </div>
        </div>
        <span className="flex items-center gap-1 text-xs text-muted"><Clock size={12} />requested {ago(approval.requestedAt)}</span>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {Object.entries(approval.arguments).map(([k, v]) => (
          <div key={k} className="rounded-xl bg-panel px-3 py-2 ring-1 ring-line">
            <dt className="text-[11px] uppercase tracking-wide text-muted">{label(k)}</dt>
            <dd className="font-mono text-sm font-semibold">
              {mode === "edit" && typeof v === "number" ? (
                <input type="number" min={0} aria-label={`New ${label(k)}`} value={String(edited[k] ?? v)} onChange={(e) => setEdited({ ...edited, [k]: Number(e.target.value) })}
                  className="w-full rounded-md border border-line bg-bg px-1.5 py-0.5 outline-none focus:border-brand" />
              ) : k.toLowerCase().includes("amount") && typeof v === "number" ? `$${v}` : String(v)}
            </dd>
          </div>
        ))}
      </dl>

      {mode === "reject" && (
        <input autoFocus placeholder="Reason (optional, shown to the agent)" value={reason} onChange={(e) => setReason(e.target.value)}
          className="mt-3 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm outline-none focus:border-brand" />
      )}
      {error && <p role="alert" className="mt-2 text-sm text-rose-600 dark:text-rose-300">{error}</p>}

      <div className="mt-3 flex flex-wrap gap-2">
        {mode === "idle" && (
          <>
            <Button variant="success" disabled={busy} onClick={() => decide("approved")}><Check size={15} />Approve</Button>
            <Button variant="danger" disabled={busy} onClick={() => setMode("reject")}><X size={15} />Reject</Button>
            {Object.values(approval.arguments).some((v) => typeof v === "number") && <Button variant="ghost" disabled={busy} onClick={() => setMode("edit")}><Pencil size={14} />Edit & approve</Button>}
          </>
        )}
        {mode === "reject" && (<><Button variant="danger" disabled={busy} onClick={() => decide("rejected")}>Confirm reject</Button><Button variant="ghost" onClick={() => setMode("idle")}>Cancel</Button></>)}
        {mode === "edit" && (<><Button variant="success" disabled={busy} onClick={() => decide("modified")}>Approve edited</Button><Button variant="ghost" onClick={() => { setMode("idle"); setEdited(approval.arguments); }}>Cancel</Button></>)}
      </div>
    </div>
  );
}

export function ApprovalsPage({ agents, onChange }: { agents: AgentInfo[]; onChange: () => void }) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [done, setDone] = useState<RunSummary[]>([]);
  const load = useCallback(() => api<RunSummary[]>("/api/approvals").then(setRuns).catch(() => setRuns([])), []);
  useEffect(() => { void load(); }, [load]);
  const name = (id: string) => agents.find((a) => a.id === id)?.name ?? id;

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Approvals</h1>
        <p className="mt-1 text-sm text-muted">Actions paused by policy. Nothing runs until you decide, and approving twice still runs once.</p>
      </header>
      {runs === null ? <p className="text-sm text-muted">Loading…</p> : runs.length === 0 && done.length === 0 ? (
        <Empty icon={<Inbox size={22} />} title="Nothing waiting">Try “Refund order ord-42” in the Support chat to see an approval appear here.</Empty>
      ) : (
        <div className="space-y-4">
          {runs.flatMap((run) => run.pendingApprovals.map((a) => (
            <Card key={a.approvalId} className="p-4">
              <p className="mb-3 text-sm text-muted">“{run.input}” · <a className="text-brand hover:underline" href={`#/runs/${run.runId}`}>view run</a></p>
              <ApprovalCard run={run} approval={a} agentName={name(run.agentId)} onDecided={(after) => { setDone((d) => [after, ...d]); onChange(); void load(); }} />
            </Card>
          )))}
          {done.map((r) => (
            <Card key={r.runId} className="rise flex items-center justify-between gap-3 p-4 text-sm">
              <span className="text-muted">Decided · {String(r.output ?? r.status)}</span>
              <a className="shrink-0 text-brand hover:underline" href={`#/runs/${r.runId}`}>View run</a>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
