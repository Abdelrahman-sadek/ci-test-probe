import { ArrowUp, BookOpen, ChevronDown, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, followRun, post, type AgentEvent, type AgentInfo, type RunDetail, type RunSummary } from "../api";
import { Card, StatusBadge, Timeline, cx, money, outputText } from "../ui";
import { ApprovalCard } from "./Approvals";

interface Turn {
  runId: string;
  input: string;
  events: AgentEvent[];
  state: RunSummary | null;
  open: boolean;
}

export function ChatPage({ agents, agentId, onChange }: { agents: AgentInfo[]; agentId: string; onChange: () => void }) {
  const agent = agents.find((a) => a.id === agentId) ?? agents[0];
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const stops = useRef<(() => void)[]>([]);

  useEffect(() => {
    setTurns([]);
    return () => stops.current.forEach((s) => s());
  }, [agentId]);
  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [turns]);

  const update = (runId: string, fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.runId === runId ? fn(t) : t)));

  const send = async (input: string) => {
    if (!agent || input.trim() === "") return;
    setError(null);
    setText("");
    try {
      const { runId } = await post<{ runId: string }>(`/api/agents/${agent.id}/runs`, { input });
      setTurns((ts) => [...ts.map((t) => ({ ...t, open: false })), { runId, input, events: [], state: null, open: true }]);
      stops.current.push(followRun(runId,
        (e) => update(runId, (t) => (t.events.some((x) => x.eventId === e.eventId) ? t : { ...t, events: [...t.events, e] })),
        (s) => { update(runId, (t) => ({ ...t, state: s })); onChange(); }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the run.");
      setText(input);
    }
  };

  const afterDecision = async (runId: string) => {
    const detail = await api<RunDetail>(`/api/runs/${runId}`);
    update(runId, (t) => ({ ...t, events: detail.events, state: detail }));
    onChange();
  };

  if (!agent) return null;
  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-line bg-panel/70 px-4 py-3 backdrop-blur sm:px-8">
        <div className="min-w-0 flex-1">
          <h1 className="font-semibold">{agent.name}</h1>
          <p className="truncate text-xs text-muted">{agent.description}</p>
        </div>
        <div role="tablist" className="flex rounded-xl bg-panel-2 p-1">
          {agents.map((a) => (
            <a key={a.id} role="tab" aria-selected={a.id === agent.id} href={`#/chat/${a.id}`}
              className={cx("rounded-lg px-3 py-1.5 text-sm font-medium transition", a.id === agent.id ? "bg-panel text-ink shadow-sm" : "text-muted hover:text-ink")}>{a.name}</a>
          ))}
        </div>
      </header>

      <div className="scroll-thin flex-1 overflow-y-auto px-4 py-6 sm:px-8">
        <div className="mx-auto max-w-3xl space-y-6">
          {turns.length === 0 && (
            <div className="py-10 text-center">
              <span className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-2xl bg-brand/10 text-brand"><Sparkles size={20} /></span>
              <p className="font-medium">Ask {agent.name} something</p>
              <p className="mt-1 text-sm text-muted">Every step it takes shows up live below its answer.</p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {agent.examples.map((ex) => (
                  <button key={ex} onClick={() => void send(ex)} className="rounded-full border border-line bg-panel px-3 py-1.5 text-sm hover:border-brand hover:text-brand">{ex}</button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t) => {
            const status = t.state?.status ?? "RUNNING";
            const live = t.state === null;
            return (
              <div key={t.runId} className="space-y-3">
                <div className="flex justify-end"><p className="rise max-w-[85%] rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-sm text-brand-ink">{t.input}</p></div>
                <Card className="rise overflow-hidden">
                  <div className="space-y-3 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={status} />
                      {t.state && <span className="text-xs text-muted tabular-nums">{t.state.usage.totalTokens} tokens · {t.state.usage.toolCalls} tool calls · {money(t.state.usage.estimatedCostUsd)}</span>}
                      <a href={`#/runs/${t.runId}`} className="ml-auto text-xs text-brand hover:underline">Details</a>
                    </div>
                    {t.state?.output != null && <p className="whitespace-pre-wrap text-[15px] leading-relaxed">{outputText(t.state.output)}</p>}
                    {t.state?.error && <p className="rounded-xl bg-rose-500/10 px-3 py-2 text-sm text-rose-700 dark:text-rose-300">{t.state.error.message}</p>}
                    {t.state && t.state.sources.length > 0 && (
                      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted"><BookOpen size={13} />Sources:
                        {t.state.sources.map((s, i) => <span key={s.title} className="rounded-full bg-panel-2 px-2 py-0.5">[{i + 1}] {s.title}</span>)}
                      </div>
                    )}
                    {t.state?.status === "WAITING_FOR_APPROVAL" && t.state.pendingApprovals.map((a) => (
                      <ApprovalCard key={a.approvalId} run={t.state!} approval={a} agentName={agent.name} onDecided={() => void afterDecision(t.runId)} />
                    ))}
                  </div>
                  <div className="border-t border-line bg-panel-2/50">
                    <button onClick={() => update(t.runId, (x) => ({ ...x, open: !x.open }))} className="flex w-full items-center gap-2 px-4 py-2 text-xs font-medium text-muted hover:text-ink" aria-expanded={t.open}>
                      <ChevronDown size={14} className={cx("transition", !t.open && "-rotate-90")} />What the agent did
                    </button>
                    {t.open && <div className="px-4 pb-4"><Timeline events={t.events} live={live} /></div>}
                  </div>
                </Card>
              </div>
            );
          })}
          <div ref={bottom} />
        </div>
      </div>

      <form onSubmit={(e) => { e.preventDefault(); void send(text); }} className="border-t border-line bg-panel px-4 py-3 sm:px-8">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <label htmlFor="msg" className="sr-only">Message</label>
          <textarea id="msg" rows={1} value={text} maxLength={2000} placeholder={`Message ${agent.name}…`} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(text); } }}
            className="max-h-40 min-h-[44px] flex-1 resize-none rounded-2xl border border-line bg-bg px-4 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
          <button type="submit" disabled={text.trim() === ""} aria-label="Send" className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-brand text-brand-ink transition hover:opacity-90 disabled:opacity-40"><ArrowUp size={18} /></button>
        </div>
        {error && <p role="alert" className="mx-auto mt-2 max-w-3xl text-sm text-rose-600 dark:text-rose-300">{error}</p>}
      </form>
    </div>
  );
}
