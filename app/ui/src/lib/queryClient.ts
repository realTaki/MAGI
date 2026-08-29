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

// Per-MAGI selection in the WebUI: which MAGI's runtime proxy
// ``runtimeUrl`` routes to. Persisted under a stable localStorage key
// so the operator's pinned MAGI survives a reload.
const TARGET_STORAGE_KEY = "magi.selected-magi-id";

function _readSelectedMagiId(): number {
  if (typeof window === "undefined") return 1;
  const raw = window.localStorage.getItem(TARGET_STORAGE_KEY);
  const parsed = Number(raw ?? "1");
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1;
}

let selectedMagiId = _readSelectedMagiId();

export function getSelectedMagiId(): number {
  return selectedMagiId;
}

export function setSelectedMagiId(magiId: number): void {
  selectedMagiId = magiId;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(TARGET_STORAGE_KEY, String(magiId));
  }
}

function isControlPath(url: string): boolean {
  if (url === "/api/magi" || /^\/api\/magi\/\d+(?:\/|$)/.test(url)) {
    return true;
  }
  return ["/api/auth", "/api/runtime", "/api/magis"].some(
    (prefix) => url === prefix || url.startsWith(`${prefix}/`) || url.startsWith(`${prefix}?`),
  );
}

function runtimeUrl(url: string): string {
  if (!url.startsWith("/api/") || isControlPath(url)) return url;
  return `/api/runtime/${getSelectedMagiId()}${url.slice(4)}`;
}

function runtimeKey<T extends readonly unknown[]>(...key: T): readonly ["runtime", number, ...T] {
  return ["runtime", getSelectedMagiId(), ...key];
}

/** Typed fetch wrapper that throws on non-2xx.
 *
 * Every request gets an ``AbortController`` with a generous
 * 30 s timeout.  The server-side control-plane proxy uses a
 * 10 s read budget (see ``CONTROL_TIMEOUT`` in
 * :mod:`runtime_http`), so 30 s gives every proxy hop +
 * the target runtime + network RTT ample headroom while
 * still preventing a hung socket from locking the button
 * into "logging in..." forever. */
export async function apiFetch<T>(
  url: string,
  init?: Omit<RequestInit, "body"> & { body?: unknown; timeoutMs?: number },
): Promise<T> {
  const { body, timeoutMs = 30_000, ...rest } = init ?? {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(runtimeUrl(url), {
      ...rest,
      credentials: "include",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(rest.headers as Record<string, string> | undefined),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  if (!r.ok) {
    const err = await r.json().catch(() => ({})) as { detail?: unknown; code?: string; message?: string };
    // Always lead with ``HTTP <status>`` so console errors are
    // grep-able by status code, regardless of whether the
    // backend sent a string ``detail``/``message`` body, a
    // structured object, or no body at all. ``.status`` is
    // preserved on the Error for programmatic consumers.
    let detail: string | undefined;
    if (typeof err.detail === "string") {
      detail = err.detail;
    } else if (typeof err.message === "string") {
      detail = err.code ? `${err.code}: ${err.message}` : err.message;
    } else if (err.detail !== undefined) {
      detail = JSON.stringify(err.detail);
    } else if (err.message !== undefined) {
      detail = JSON.stringify(err.message);
    }
    const msg = detail ? `HTTP ${r.status}: ${detail}` : `HTTP ${r.status}`;
    throw Object.assign(new Error(msg), { status: r.status });
  }
  return r.json() as T;
  } catch (err) {
  if (err instanceof DOMException && err.name === "AbortError") {
    throw new Error("Request timed out — the server did not respond in time");
  }
  throw err;
  } finally {
  clearTimeout(timer);
  }
}

/** Stable query-key factory so every caller uses the same keys. */
export const qk = {
  me: ["me"] as const,
  contacts: (withNotes?: boolean) =>
    runtimeKey("contacts", { withNotes }),
  magis: ["magis"] as const,
  magi: ["magi"] as const,
  /** Legacy alias for ``magi`` — preserved so older call sites that
   *  cache by the old key continue to invalidate correctly. */
  magic: ["magi"] as const,
  skills: runtimeKey("skills"),
  memory: runtimeKey("memory"),
  tasks: (filter?: { enabled?: boolean }) =>
    filter ? runtimeKey("tasks", filter) : runtimeKey("tasks"),
  taskRuns: (taskId: string) =>
    runtimeKey("taskRuns", taskId),
  /** Single task by id — separate from the list cache
   *  so a per-row fetch doesn't refetch the whole list. */
  task: (taskId: string) =>
    runtimeKey("task", taskId),
  /** Active conversation messages — paginated per conversation. */
  chatMessages: (conversationId: number) =>
    runtimeKey("chatMessages", conversationId),
  chatConversations: (limit?: number, offset?: number) =>
    limit === undefined && offset === undefined
      ? runtimeKey("chatConversations")
      : runtimeKey("chatConversations", { limit, offset }),
  /** Full conversation detail (messages included). */
  chatConversation: (conversationId: number) =>
    runtimeKey("chatConversation", conversationId),
  /** Chat search results — keyed by query string so a
   *  re-typed query hits the cache. */
  chatSearch: (q: string) =>
    runtimeKey("chatSearch", q),
  actionItems: runtimeKey("actionItems"),
  systemSettings: (key: string) =>
    runtimeKey("systemSettings", key),
  mcpServers: runtimeKey("mcpServers"),
  // -- auth / soul ------------------------------------------------------------
  availableMagi: ["auth", "available-magi"] as const,
  targetAccounts: (magiId: number) => ["auth", "target-accounts", magiId] as const,
  soul: runtimeKey("soul"),
  tgReaction: (kind: "read" | "done") =>
    runtimeKey("tgReaction", kind),
};
