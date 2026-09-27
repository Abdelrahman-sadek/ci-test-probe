import { ArrowLeft, History, ScrollText } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type AgentInfo, type RunDetail, type RunSummary } from "../api";
import { Card, Empty, StatusBadge, Timeline, ago, money, outputText } from "../ui";

const name = (agents: AgentInfo[], id: string) => agents.find((a) => a.id === id)?.name ?? id;

export function RunsPage({ agents }: { agents: AgentInfo[] }) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [filter, setFilter] = useState("all");
  useEffect(() => {
    const load = () => api<RunSummary[]>("/api/runs").then(setRuns).catch(() => setRuns([]));
    void load();
    const t = setInterval(load, 5_000);
    return () => clearInterval(t);
  }, []);
  const shown = (runs ?? []).filter((r) => filter === "all" || r.status === filter);
  const totals = (runs ?? []).reduce((a, r) => ({ cost: a.cost + r.usage.estimatedCostUsd, tokens: a.tokens + r.usage.totalTokens }), { cost: 0, tokens: 0 });

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
          <p className="mt-1 text-sm text-muted">Every run is saved with its steps, tool audit and cost.</p>
        </div>
        <select aria-label="Filter by status" value={filter} onChange={(e) => setFilter(e.target.value)} className="rounded-xl border border-line bg-panel px-3 py-2 text-sm">
          <option value="all">All statuses</option><option value="COMPLETED">Completed</option><option value="WAITING_FOR_APPROVAL">Needs approval</option><option value="FAILED">Failed</option>
        </select>
      </header>

      <div className="mb-4 grid grid-cols-3 gap-3">
        {[["Runs", String(runs?.length ?? 0)], ["Tokens", totals.tokens.toLocaleString()], ["Est. cost", money(totals.cost)]].map(([k, v]) => (
          <Card key={k} className="px-4 py-3"><div className="text-xs text-muted">{k}</div><div className="text-xl font-semibold tabular-nums">{v}</div></Card>
        ))}
      </div>

      {runs !== null && shown.length === 0 ? <Empty icon={<History size={22} />} title="No runs yet">Start a chat and each message becomes a run here.</Empty> : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-panel-2 text-xs text-muted"><tr><th className="px-4 py-2.5 font-medium">Status</th><th className="px-4 py-2.5 font-medium">Agent</th><th className="px-4 py-2.5 font-medium">Message</th><th className="px-4 py-2.5 text-right font-medium">Tokens</th><th className="px-4 py-2.5 text-right font-medium">Started</th></tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.runId} className="cursor-pointer border-t border-line hover:bg-panel-2/60" onClick={() => { window.location.hash = `#/runs/${r.runId}`; }}>
                    <td className="px-4 py-3"><StatusBadge status={r.status} /></td>
                    <td className="px-4 py-3 whitespace-nowrap">{name(agents, r.agentId)}</td>
                    <td className="max-w-[22rem] truncate px-4 py-3"><a href={`#/runs/${r.runId}`} className="hover:underline">{r.input}</a></td>
                    <td className="px-4 py-3 text-right tabular-nums">{r.usage.totalTokens}</td>
                    <td className="px-4 py-3 text-right whitespace-nowrap text-muted">{ago(r.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

export function RunDetailPage({ runId, agents }: { runId: string; agents: AgentInfo[] }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<RunDetail>(`/api/runs/${runId}`).then(setRun).catch((e: Error) => setError(e.message)); }, [runId]);
  if (error) return <div className="p-8 text-sm text-rose-600">{error}</div>;
  if (!run) return <div className="p-8 text-sm text-muted">Loading…</div>;
  const seconds = (Date.parse(run.updatedAt) - Date.parse(run.createdAt)) / 1000;

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8">
      <a href="#/runs" className="mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-ink"><ArrowLeft size={15} />All runs</a>
      <header className="mb-6 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight">{name(agents, run.agentId)}</h1>
        <StatusBadge status={run.status} />
        <code className="ml-auto text-xs text-muted">{run.runId}</code>
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-4">
          <Card className="p-5">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted">Message</p>
            <p className="mt-1">{run.input}</p>
            {run.output != null && <><p className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted">Answer</p><p className="mt-1 whitespace-pre-wrap">{outputText(run.output)}</p></>}
            {run.error && <p className="mt-4 rounded-xl bg-rose-500/10 px-3 py-2 text-sm text-rose-700 dark:text-rose-300">{run.error.code}: {run.error.message}</p>}
          </Card>
          <Card className="p-5">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">Timeline</p>
            <Timeline events={run.events} />
          </Card>
        </div>
        <div className="space-y-4">
          <Card className="grid grid-cols-2 gap-3 p-5 text-sm">
            {[["Tokens", run.usage.totalTokens], ["Est. cost", money(run.usage.estimatedCostUsd)], ["Model calls", run.usage.llmCalls], ["Tool calls", run.usage.toolCalls], ["Duration", `${seconds.toFixed(2)}s`], ["Started", ago(run.createdAt)]].map(([k, v]) => (
              <div key={String(k)}><div className="text-xs text-muted">{k}</div><div className="font-semibold tabular-nums">{v}</div></div>
            ))}
          </Card>
          <Card className="p-5">
            <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted"><ScrollText size={13} />Tool audit</p>
            {run.audit.length === 0 ? <p className="text-sm text-muted">No tools were called.</p> : (
              <ul className="space-y-3">
                {run.audit.map((a) => (
                  <li key={a.auditId} className="text-sm">
                    <div className="flex items-center justify-between gap-2"><code className="text-[12px]">{a.toolName}</code><span className="text-xs text-muted">{a.outcome.replace(/_/g, " ")}</span></div>
                    <div className="mt-0.5 text-xs text-muted">
                      {a.authorization && <>{a.authorization.allowed ? "Allowed" : "Denied"} · {a.authorization.reason}</>}
                      {a.approval && <> · {a.approval.decision}{a.approval.decidedBy ? ` by ${a.approval.decidedBy}` : ""}</>}
                    </div>
                    {a.input !== undefined && <code className="mt-1 block truncate text-[11px] text-muted">{JSON.stringify(a.input)}</code>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
