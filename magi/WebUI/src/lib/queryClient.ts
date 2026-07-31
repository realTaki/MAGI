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

/** Typed fetch wrapper that throws on non-2xx. */
export async function apiFetch<T>(
  url: string,
  init?: Omit<RequestInit, "body"> & { body?: unknown },
): Promise<T> {
  const { body, ...rest } = init ?? {};
  const r = await fetch(url, {
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
    ["contacts", { withNotes }] as const,
  magis: ["magis"] as const,
  magic: ["magic"] as const,
  skills: ["skills"] as const,
  memory: ["memory"] as const,
  tasks: (filter?: { enabled?: boolean; kind?: "preset" | "custom" }) =>
    filter ? ["tasks", filter] as const : ["tasks"] as const,
  taskRuns: (taskId: string) =>
    ["taskRuns", taskId] as const,
  /** Single task by id — separate from the list cache
   *  so a per-row fetch doesn't refetch the whole list. */
  task: (taskId: string) =>
    ["task", taskId] as const,
  /** Global preset templates — the operator-editable
   *  source of truth for the auto-seeded per-user
   *  "预设任务" rows. Settings → 任务预设 drives this
   *  cache; mutations invalidate on success. */
  taskPresets: ["taskPresets"] as const,
  /** Active session messages — paginated per session. */
  chatMessages: (sessionId: string) =>
    ["chatMessages", sessionId] as const,
  chatSessions: (limit?: number, offset?: number) =>
    limit === undefined && offset === undefined
      ? (["chatSessions"] as const)
      : (["chatSessions", { limit, offset }] as const),
  /** Full session detail (messages included). */
  chatSession: (sessionId: string) =>
    ["chatSession", sessionId] as const,
  /** Chat search results — keyed by query string so a
   *  re-typed query hits the cache. */
  chatSearch: (q: string) =>
    ["chatSearch", q] as const,
  actionItems: ["actionItems"] as const,
  systemSettings: (key: string) =>
    ["systemSettings", key] as const,
  mcpServers: ["mcpServers"] as const,
  // -- auth / onboarding / soul ---------------------------------------------
  allowedAccounts: ["auth", "allowed-accounts"] as const,
  onboardingStatus: ["onboarding", "status"] as const,
  soul: ["soul"] as const,
  tgReaction: (kind: "read" | "done") =>
    ["tgReaction", kind] as const,
};
