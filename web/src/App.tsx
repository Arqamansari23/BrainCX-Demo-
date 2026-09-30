import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, SOURCE_ICONS, api, type Board, type Deal, type LiveEvent, type Stage, type Task } from "./api";
import { PipelineBoard } from "./Board";
import { Contacts, LeadForm } from "./Contacts";
import { useLiveUpdates } from "./live";
import { SidePanel } from "./SidePanel";
import { VoicePanel } from "./VoicePanel";

export default function App() {
  const [auth, setAuth] = useState<"checking" | "in" | "out">("checking");

  useEffect(() => {
    api
      .get<{ logged_in: boolean }>("/api/auth/me")
      .then((r) => setAuth(r.logged_in ? "in" : "out"))
      .catch(() => setAuth("out"));
  }, []);

  if (auth === "checking") return <div className="splash">Loading…</div>;
  if (auth === "out") return <Login onLoggedIn={() => setAuth("in")} />;
  return <Crm onLoggedOut={() => setAuth("out")} />;
}

function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.post("/api/auth/login", { password });
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    }
  };

  return (
    <div className="splash">
      <form className="card login" onSubmit={submit}>
        <h1>🎙️ VoiceCRM</h1>
        <p className="muted">A small CRM you can update by voice.</p>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoComplete="current-password"
          />
        </label>
        {error && <p className="error-text">{error}</p>}
        <button className="btn primary" type="submit">
          Log in
        </button>
      </form>
    </div>
  );
}

type Toast = LiveEvent & { id: number };

function Crm({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [tab, setTab] = useState<"pipeline" | "contacts">("pipeline");
  const [showLeadForm, setShowLeadForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [flash, setFlash] = useState<Set<number>>(new Set());
  const refreshTimer = useRef<ReturnType<typeof setTimeout>>();

  const load = useCallback(async () => {
    try {
      setBoard(await api.get<Board>("/api/board"));
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onLoggedOut();
      else setError(err instanceof Error ? err.message : "Could not load the CRM");
    }
  }, [onLoggedOut]);

  // Several events can arrive together (a stage change plus its automations),
  // so refetch once after they settle.
  const refresh = useCallback(() => {
    clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(load, 150);
  }, [load]);

  const connected = useLiveUpdates((event) => {
    refresh();
    const id = Date.now() + Math.random();
    setToasts((current) => [...current.slice(-3), { ...event, id }]);
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 6000);
    const dealId = event.opportunity_id;
    if (dealId) {
      setFlash((current) => new Set(current).add(dealId));
      setTimeout(
        () =>
          setFlash((current) => {
            const next = new Set(current);
            next.delete(dealId);
            return next;
          }),
        3000,
      );
    }
  });

  // Load on start, and catch up on anything missed whenever the socket (re)connects.
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (connected) refresh();
  }, [connected, refresh]);

  // The live event that follows each change refreshes the board.
  const run = async (action: () => Promise<unknown>) => {
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    }
  };
  const changeStage = (deal: Deal, stage: Stage) =>
    run(() => api.patch(`/api/opportunities/${deal.id}`, { stage }));
  const completeTask = (task: Task) => run(() => api.patch(`/api/tasks/${task.id}`, { done: true }));

  const logout = async () => {
    await api.post("/api/auth/logout");
    onLoggedOut();
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">🎙️ VoiceCRM</div>
        <nav className="tabs">
          <button className={tab === "pipeline" ? "active" : ""} onClick={() => setTab("pipeline")}>
            Pipeline
          </button>
          <button className={tab === "contacts" ? "active" : ""} onClick={() => setTab("contacts")}>
            Contacts
          </button>
        </nav>
        <div className="topbar-right">
          <span className={`live ${connected ? "on" : ""}`}>{connected ? "Live" : "Reconnecting…"}</span>
          <button className="btn" onClick={() => setShowLeadForm((v) => !v)}>
            + New lead
          </button>
          <button className="btn ghost" onClick={logout}>
            Log out
          </button>
        </div>
      </header>

      {error && (
        <div className="banner" role="alert">
          {error}
          <button className="link" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      <main className="layout">
        <section className="main">
          {showLeadForm && <LeadForm onDone={() => setShowLeadForm(false)} />}
          {!board ? (
            <p className="muted">Loading…</p>
          ) : tab === "pipeline" ? (
            <PipelineBoard board={board} flash={flash} onChangeStage={changeStage} />
          ) : (
            <Contacts contacts={board.contacts} />
          )}
        </section>
        {/* The voice panel sits at the top of the right column, in the normal page
            flow, so opening it pushes Follow-ups and Activity down instead of covering them. */}
        <aside className="side">
          <VoicePanel />
          {board && <SidePanel board={board} onComplete={completeTask} />}
        </aside>
      </main>

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast src-${t.actor}`}>
            <span>{SOURCE_ICONS[t.actor] ?? "•"}</span> {t.summary}
          </div>
        ))}
      </div>
    </div>
  );
}
