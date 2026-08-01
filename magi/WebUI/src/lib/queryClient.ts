import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 30 s is the default; reads feel "live" without
      // hammering the API. The per-server MCP tool list
      // hook overrides this to 5 min because MCP config
      // is operator-edited and the round-trip can be
      // slow (the loader falls back to on-demand
      // connect when the subprocess connection is
      // closed).
      staleTime: 30_000,
      refetchOnWindowFocus: true,
      retry: 1,
      // Keep fetched data in memory for 30 min after the
      // last subscriber unmounts. The operator may
      // bounce between Knowledge and Settings tabs;
      // re-mounting should hit the cache, not the
      // server.
      gcTime: 30 * 60 * 1000,
    },
  },
});

const TARGET_STORAGE_KEY = "magi.selected-magic-id";
let selectedMagicId = Number(
  typeof window === "undefined" ? 1 : window.localStorage.getItem(TARGET_STORAGE_KEY) ?? "1",
);

export function getSelectedMagicId(): number {
  return Number.isInteger(selectedMagicId) && selectedMagicId > 0 ? selectedMagicId : 1;
}

export function setSelectedMagicId(magicId: number): void {
  selectedMagicId = magicId;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(TARGET_STORAGE_KEY, String(magicId));
  }
}

function isControlPath(url: string): boolean {
  return ["/api/auth", "/api/onboarding", "/api/runtime"].some(
    (prefix) => url === prefix || url.startsWith(`${prefix}/`) || url.startsWith(`${prefix}?`),
  );
}

function runtimeUrl(url: string): string {
  if (!url.startsWith("/api/") || isControlPath(url)) return url;
  return `/api/runtime/${getSelectedMagicId()}${url.slice(4)}`;
}

function runtimeKey<T extends readonly unknown[]>(...key: T): readonly ["runtime", number, ...T] {
  return ["runtime", getSelectedMagicId(), ...key];
}

/** Typed fetch wrapper that throws on non-2xx. */
export async function apiFetch<T>(
  url: string,
  init?: Omit<RequestInit, "body"> & { body?: unknown },
): Promise<T> {
  const { body, ...rest } = init ?? {};
  const r = await fetch(runtimeUrl(url), {
    ...rest,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(rest.headers as Record<string, string> | undefined),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({})) as { detail?: unknown; code?: string; message?: string };
    const msg = typeof err.detail === "string" ? err.detail
      : typeof err.message === "string" ? `${err.code ?? r.status}: ${err.message}`
      : JSON.stringify(err.detail ?? err.message ?? `HTTP ${r.status}`);
    throw Object.assign(new Error(msg), { status: r.status });
  }
  return r.json() as T;
}

/** Stable query-key factory so every caller uses the same keys. */
export const qk = {
  me: ["me"] as const,
  contacts: (withNotes?: boolean) =>
    runtimeKey("contacts", { withNotes }),
  magis: ["magis"] as const,
  magic: ["magic"] as const,
  skills: runtimeKey("skills"),
  memory: runtimeKey("memory"),
  tasks: (filter?: { enabled?: boolean; kind?: "preset" | "custom" }) =>
    filter ? runtimeKey("tasks", filter) : runtimeKey("tasks"),
  taskRuns: (taskId: string) =>
    runtimeKey("taskRuns", taskId),
  /** Single task by id — separate from the list cache
   *  so a per-row fetch doesn't refetch the whole list. */
  task: (taskId: string) =>
    runtimeKey("task", taskId),
  /** Global preset templates — the operator-editable
   *  source of truth for the auto-seeded per-user
   *  "预设任务" rows. Settings → 任务预设 drives this
   *  cache; mutations invalidate on success. */
  taskPresets: runtimeKey("taskPresets"),
  /** Active session messages — paginated per session. */
  chatMessages: (sessionId: string) =>
    runtimeKey("chatMessages", sessionId),
  chatSessions: (limit?: number, offset?: number) =>
    limit === undefined && offset === undefined
      ? runtimeKey("chatSessions")
      : runtimeKey("chatSessions", { limit, offset }),
  /** Full session detail (messages included). */
  chatSession: (sessionId: string) =>
    runtimeKey("chatSession", sessionId),
  /** Chat search results — keyed by query string so a
   *  re-typed query hits the cache. */
  chatSearch: (q: string) =>
    runtimeKey("chatSearch", q),
  actionItems: runtimeKey("actionItems"),
  systemSettings: (key: string) =>
    runtimeKey("systemSettings", key),
  mcpServers: runtimeKey("mcpServers"),
  // -- auth / onboarding / soul ---------------------------------------------
  allowedAccounts: ["auth", "allowed-accounts"] as const,
  availableMagi: ["auth", "available-magi"] as const,
  targetAccounts: (magicId: number) => ["auth", "target-accounts", magicId] as const,
  onboardingStatus: ["onboarding", "status"] as const,
  soul: runtimeKey("soul"),
  tgReaction: (kind: "read" | "done") =>
    runtimeKey("tgReaction", kind),
};
