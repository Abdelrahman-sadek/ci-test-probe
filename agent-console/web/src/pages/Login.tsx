import { Lock, Moon, Sun, Zap } from "lucide-react";
import { useState } from "react";
import { post } from "../api";
import { Button } from "../ui";

export function LoginPage({ onSignedIn, dark, onToggleTheme }: { onSignedIn: () => void; dark: boolean; onToggleTheme: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await post("/api/login", { password });
      onSignedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative grid min-h-full place-items-center px-4 py-10">
      <button onClick={onToggleTheme} className="absolute right-4 top-4 rounded-xl p-2 text-muted hover:bg-panel-2" aria-label="Toggle theme">{dark ? <Sun size={18} /> : <Moon size={18} />}</button>
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <span className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-brand text-brand-ink shadow-lg shadow-indigo-500/20"><Zap size={24} /></span>
          <h1 className="text-2xl font-semibold tracking-tight">Agent Console</h1>
          <p className="mt-1 text-sm text-muted">Run agents, approve risky actions, and see every step.</p>
        </div>
        <form onSubmit={submit} className="rounded-2xl border border-line bg-panel p-6 shadow-sm">
          <label htmlFor="password" className="mb-1.5 block text-sm font-medium">Admin password</label>
          <div className="relative">
            <Lock size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input id="password" type="password" autoFocus autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-xl border border-line bg-bg py-2.5 pl-9 pr-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
          </div>
          {error && <p role="alert" className="mt-2 text-sm text-rose-600 dark:text-rose-300">{error}</p>}
          <Button type="submit" disabled={busy || password === ""} className="mt-4 w-full py-2.5">{busy ? "Signing in…" : "Sign in"}</Button>
        </form>
      </div>
    </div>
  );
}
