import { Bot, Hand, History, LogOut, MessageSquare, Moon, Sun, Zap } from "lucide-react";
import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, post, type AgentInfo, type RunSummary } from "./api";
import { AgentsPage } from "./pages/Agents";
import { ApprovalsPage } from "./pages/Approvals";
import { ChatPage } from "./pages/Chat";
import { LoginPage } from "./pages/Login";
import { RunDetailPage, RunsPage } from "./pages/Runs";
import "./styles.css";
import { cx } from "./ui";

function useHashRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const on = () => setParts(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}

function useTheme(): [boolean, () => void] {
  const initial = () => {
    try {
      const saved = localStorage.getItem("theme");
      if (saved !== null) return saved === "dark";
    } catch { /* storage unavailable */ }
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  };
  const [dark, setDark] = useState(initial);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch { /* ignore */ }
  }, [dark]);
  return [dark, () => setDark((d) => !d)];
}

function App() {
  const [dark, toggleTheme] = useTheme();
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [waiting, setWaiting] = useState(0);
  const route = useHashRoute();

  const refreshWaiting = useCallback(() => {
    api<RunSummary[]>("/api/approvals").then((r) => setWaiting(r.reduce((n, run) => n + run.pendingApprovals.length, 0))).catch(() => {});
  }, []);

  useEffect(() => {
    api<AgentInfo[]>("/api/agents").then((a) => { setAgents(a); setSignedIn(true); }).catch(() => setSignedIn(false));
    const out = () => setSignedIn(false);
    window.addEventListener("signed-out", out);
    return () => window.removeEventListener("signed-out", out);
  }, []);
  useEffect(() => {
    if (!signedIn) return;
    refreshWaiting();
    const t = setInterval(refreshWaiting, 5_000);
    return () => clearInterval(t);
  }, [signedIn, refreshWaiting]);

  if (signedIn === null) return null;
  if (!signedIn) return <LoginPage dark={dark} onToggleTheme={toggleTheme} onSignedIn={() => api<AgentInfo[]>("/api/agents").then((a) => { setAgents(a); setSignedIn(true); })} />;

  const [section = "agents", id] = route;
  const nav = [
    { key: "agents", label: "Agents", icon: <Bot size={18} />, href: "#/agents" },
    { key: "chat", label: "Chat", icon: <MessageSquare size={18} />, href: `#/chat/${agents[0]?.id ?? "support"}` },
    { key: "approvals", label: "Approvals", icon: <Hand size={18} />, href: "#/approvals", badge: waiting },
    { key: "runs", label: "Runs", icon: <History size={18} />, href: "#/runs" },
  ];

  let page;
  if (section === "chat") page = <ChatPage agents={agents} agentId={id ?? agents[0]?.id ?? "support"} onChange={refreshWaiting} />;
  else if (section === "approvals") page = <ApprovalsPage agents={agents} onChange={refreshWaiting} />;
  else if (section === "runs" && id) page = <RunDetailPage runId={id} agents={agents} />;
  else if (section === "runs") page = <RunsPage agents={agents} />;
  else page = <AgentsPage agents={agents} />;

  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="flex shrink-0 items-center gap-1 border-b border-line bg-panel px-3 py-2 md:w-60 md:flex-col md:items-stretch md:border-b-0 md:border-r md:px-3 md:py-5">
        <a href="#/agents" className="mr-2 flex items-center gap-2.5 px-2 md:mb-6 md:mr-0">
          <span className="grid h-8 w-8 place-items-center rounded-xl bg-brand text-brand-ink"><Zap size={17} /></span>
          <span className="hidden leading-tight sm:block">
            <span className="block text-[15px] font-semibold">Agent Console</span>
            <span className="block text-xs text-muted">agents-framework</span>
          </span>
        </a>
        <nav className="flex flex-1 gap-1 overflow-x-auto md:flex-none md:flex-col">
          {nav.map((n) => (
            <a key={n.key} href={n.href} aria-current={section === n.key ? "page" : undefined}
              className={cx("flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium whitespace-nowrap transition",
                section === n.key ? "bg-brand/10 text-brand" : "text-muted hover:bg-panel-2 hover:text-ink")}>
              {n.icon}<span className="hidden sm:inline">{n.label}</span>
              {n.badge ? <span className="ml-auto rounded-full bg-amber-500 px-1.5 text-[11px] font-semibold text-white tabular-nums">{n.badge}</span> : null}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-1 md:mt-auto md:flex-col md:items-stretch">
          <div className="hidden rounded-xl bg-panel-2 px-3 py-2.5 text-xs text-muted md:block">
            <div className="font-medium text-ink">Model</div>Scripted demo · offline
          </div>
          <button onClick={toggleTheme} className="flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-muted hover:bg-panel-2 hover:text-ink" aria-label="Toggle theme">
            {dark ? <Sun size={18} /> : <Moon size={18} />}<span className="hidden md:inline">{dark ? "Light mode" : "Dark mode"}</span>
          </button>
          <button onClick={() => post("/api/logout", {}).finally(() => setSignedIn(false))} className="flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-muted hover:bg-panel-2 hover:text-ink" aria-label="Sign out">
            <LogOut size={18} /><span className="hidden md:inline">Sign out</span>
          </button>
        </div>
      </aside>
      <main className="scroll-thin min-w-0 flex-1 overflow-y-auto">{page}</main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
