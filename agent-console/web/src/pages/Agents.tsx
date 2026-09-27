import { ArrowRight, Hand, ShieldCheck, Wrench } from "lucide-react";
import type { AgentInfo } from "../api";
import { Card } from "../ui";

export function AgentsPage({ agents }: { agents: AgentInfo[] }) {
  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
        <p className="mt-1 text-sm text-muted">Each agent can only use its listed tools, within hard limits. Risky actions wait for you.</p>
      </header>
      <div className="grid gap-4 md:grid-cols-2">
        {agents.map((a) => (
          <Card key={a.id} className="flex flex-col p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">{a.name}</h2>
                <p className="mt-1 text-sm text-muted">{a.description}</p>
              </div>
              <span className="rounded-lg bg-panel-2 px-2 py-1 font-mono text-[11px] text-muted">{a.id}</span>
            </div>

            <div className="mt-5 space-y-4 text-sm">
              <section>
                <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted"><Wrench size={13} /> Tools</h3>
                {a.tools.length === 0 ? <p className="text-muted">None, answers from documents only.</p> : (
                  <ul className="space-y-1.5">
                    {a.tools.map((t) => (
                      <li key={t.name} className="flex flex-wrap items-center gap-2">
                        <code className="rounded-md bg-panel-2 px-1.5 py-0.5 text-[12px]">{t.name}</code>
                        <span className="text-muted">{t.description}</span>
                        {t.approval && <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/14 px-2 py-0.5 text-xs text-amber-800 dark:text-amber-300"><Hand size={12} />{t.approval}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section>
                <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted"><ShieldCheck size={13} /> Protections</h3>
                <div className="flex flex-wrap gap-1.5">
                  {a.guardrails.map((g) => <span key={g} className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-800 dark:text-emerald-300">{g}</span>)}
                  <span className="rounded-full bg-panel-2 px-2 py-0.5 text-xs text-muted">≤ {a.limits.maxSteps} steps · ≤ {a.limits.maxToolCalls} tool calls</span>
                </div>
              </section>
            </div>

            <a href={`#/chat/${a.id}`} className="mt-6 inline-flex items-center gap-1.5 self-start rounded-xl bg-brand px-3.5 py-2 text-sm font-medium text-brand-ink hover:opacity-90">
              Open chat <ArrowRight size={15} />
            </a>
          </Card>
        ))}
      </div>
    </div>
  );
}
