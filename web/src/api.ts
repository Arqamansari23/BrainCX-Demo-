// Thin API client plus the shapes the backend returns. Requests go to the same
// origin (Vite proxies /api), so the session cookie is sent automatically.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// The app registers one handler that shows the login screen. Every 401 (an expired
// session, or a logout in another tab) goes through it, wherever it happens.
let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

export function notifyUnauthorized() {
  unauthorizedHandler?.();
}

function parseJson(text: string): unknown {
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return null; // e.g. a plain-text "Internal Server Error"
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: init.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  const data = parseJson(await res.text());
  if (!res.ok) {
    // A wrong password at login is a 401 too, but it isn't a lost session.
    if (res.status === 401 && path !== "/api/auth/login") notifyUnauthorized();
    const detail = data && typeof data === "object" ? (data as { detail?: unknown }).detail : undefined;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg).join("; ")
          : `Request failed (${res.status})`;
    throw new ApiError(res.status, message);
  }
  return data as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  patch: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
};

// ------------------------------------------------------------------- types

export type Stage = "new" | "contacted" | "qualified" | "proposal" | "won" | "lost";
export type Source = "ui" | "voice" | "webhook" | "automation" | "seed";

export type Deal = {
  id: number;
  title: string;
  value: number;
  stage: Stage;
  updated_at: string;
  contact_id: number;
  contact: string;
  company: string | null;
  next_task: string | null;
  next_task_due: string | null;
};

export type Task = {
  id: number;
  title: string;
  due_date: string;
  source: Source;
  contact_id: number;
  contact: string;
  deal: string | null;
};

export type Activity = {
  id: number;
  kind: string;
  message: string;
  source: Source;
  created_at: string;
};

export type Contact = {
  id: number;
  full_name: string;
  email: string | null;
  phone: string | null;
  company: string | null;
  status: "lead" | "customer";
  source: Source;
  deals: number;
  open_value: number;
};

export type Board = {
  today: string;
  stages: Stage[];
  deals: Deal[];
  tasks: Task[];
  activities: Activity[];
  contacts: Contact[];
};

// Pushed over /ws whenever anything changes, whoever changed it.
export type LiveEvent = {
  type: "crm";
  actor: Source;
  summary: string;
  opportunity_id?: number | null;
};

// ---------------------------------------------------------------- display

export const STAGE_LABELS: Record<Stage, string> = {
  new: "New",
  contacted: "Contacted",
  qualified: "Qualified",
  proposal: "Proposal",
  won: "Won",
  lost: "Lost",
};

export const SOURCE_LABELS: Record<Source, string> = {
  ui: "Web",
  voice: "Voice",
  webhook: "Webhook",
  automation: "Automation",
  seed: "Demo data",
};

export const SOURCE_ICONS: Record<Source, string> = {
  ui: "🖱️",
  voice: "🎙️",
  webhook: "🔗",
  automation: "⚙️",
  seed: "📦",
};

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export const money = (value: number) => usd.format(value);

export function shortDate(iso: string): string {
  // Dates arrive as YYYY-MM-DD; build them as local dates so they don't shift.
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}
