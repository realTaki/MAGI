/** Shared React Query hooks for the WebUI.
 *
 * Every hook here uses ``qk.*`` query keys so invalidation
 * from one surface (e.g. creating a Magi) automatically
 * refreshes the MagisPane that lists them. Components that
 * previously did their own ``fetch + useState`` now get
 * cache dedup + background revalidation for free.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ContactRow as MagicContactRow } from "../pages/AgenticSocietyTab";
import { apiFetch, qk } from "./queryClient";

// -- shared types -----------------------------------------------------------

export type ContactRow = MagicContactRow;

type ContactListResponse = { items: ContactRow[]; total: number };

// -- contacts ---------------------------------------------------------------

/** All contacts — used by the knowledge + settings surfaces. */
export function useContacts(initialData?: ContactRow[]) {
  return useQuery({
    queryKey: qk.contacts(),
    queryFn: () => apiFetch<ContactListResponse>("/api/contacts"),
    initialData: initialData ? { items: initialData, total: initialData.length } : undefined,
    select: (data) => data.items,
  });
}

/** Subset filtered to ``admin=true`` — used by the WebUI access card.

 Pre-2024 this query used ``?role=admin``; the role enum
 shrunk to ``{assigned, contact, guest}`` when admin was
 split into its own boolean. The backend's new ``?admin=true``
 filter is the canonical way to list WebUI operators. */
export function useAdminContacts() {
  return useQuery({
    queryKey: [...qk.contacts(), "admin"] as const,
    queryFn: () => apiFetch<ContactListResponse>("/api/contacts?admin=true&page=1&page_size=100"),
    select: (data) => data.items,
  });
}

// -- magis / magi ---------------------------------------------------------

export type MagisRow = {
  id: number; name: string; parent_id: number | null;
  adam_id: number | null; instruction: string; child_count: number; member_count: number;
  created_at: string; updated_at: string;
};

export function useMagis() {
  return useQuery({
    queryKey: qk.magis,
    queryFn: () => apiFetch<MagisRow[]>("/api/magis"),
  });
}

export type MagiRow = {
  id: number; name: string | null;
  provider: string | null; api_key_set: boolean; api_key_last4: string | null;
  memberships: { magis_id: number; magis_name: string; role_id: number; role_name: string }[];
  runtime: EvaRuntimeRow | null;
  created_at: string; updated_at: string;
};
/** Back-compat alias — pre-rename callers imported ``MAGICRow`` /
 *  ``MAGICBrief``. Keeps those imports compiling. */
export type MAGICRow = MagiRow;
export type MAGICBrief = MagiRow;

export function useMagi() {
  return useQuery({
    queryKey: qk.magi,
    queryFn: () => apiFetch<MagiRow[]>("/api/magi"),
  });
}
/** Back-compat alias for ``useMagi``. */
export const useMagic = useMagi;

export type EvaRuntimeRow = {
  desired_state: "draft" | "running" | "stopped" | "deleted";
  observed_state: "draft" | "provisioning" | "running" | "stopped" | "failed" | "deleting" | "deleted";
  namespace: string | null; deployment_name: string | null;
  workspace_claim_name: string | null; credential_secret_name: string | null;
  last_error: string | null; updated_at: string;
};

// -- tasks ------------------------------------------------------------------

export type TaskRow = {
  id: number; task_id: string; name: string; prompt: string;
  target_channel: "webui" | "tg";
  delivery_to: string | null; enabled: boolean;
  conversation_id: number | null;
  last_run_at: string | null; last_status: string | null;
  consecutive_failures: number;
  created_at: string; updated_at: string;
};

/** Tasks query — returns every task owned by the caller.
 *
 * ``filter.enabled`` narrows further to enabled / disabled.
 */
export function useTasks(filter?: { enabled?: boolean }) {
  return useQuery({
    queryKey: qk.tasks(filter),
    queryFn: () => {
      const params = new URLSearchParams();
      if (filter?.enabled !== undefined) {
        params.set("enabled", String(filter.enabled));
      }
      const qs = params.toString();
      return apiFetch<TaskOut[]>(`/api/tasks${qs ? "?" + qs : ""}`);
    },
  });
}

export type TaskRunRow = {
  id: string; task_id: string; conversation_id: number | null;
  manual: boolean; started_at: string; finished_at: string | null;
  latency_ms: number | null; status: string; error: string | null;
  reply_excerpt: string | null;
};

export function useTaskRuns(taskId: string) {
  return useQuery({
    queryKey: qk.taskRuns(taskId),
    queryFn: () => apiFetch<TaskRunRow[]>(`/api/tasks/${taskId}/runs`),
    enabled: !!taskId,
  });
}

// -- action items -----------------------------------------------------------

export type ActionItemRow = {
  id: number; uid: number; text: string;
  status: string; due_date: string | null;
  source: string; created_at: string; updated_at: string;
};

export function useActionItems() {
  return useQuery({
    queryKey: qk.actionItems,
    queryFn: () => apiFetch<ActionItemRow[]>("/api/action_items"),
  });
}

// -- skills -----------------------------------------------------------------

export type SkillRow = {
  name: string; description: string; path: string; version: string;
};

export function useSkills() {
  return useQuery({
    queryKey: qk.skills,
    queryFn: () => apiFetch<SkillRow[]>("/api/skills"),
  });
}

// -- system settings --------------------------------------------------------

export function useSystemTimezone() {
  return useQuery({
    queryKey: qk.systemSettings("timezone"),
    queryFn: () =>
      apiFetch<{ current: string; default: string }>(
        "/api/system-settings/timezone",
      ),
  });
}

// -- convenience: refetch multiple queries -----------------------------------

/** Invalidate magis + magic together (both panes share these). */
export function useRefreshSociety() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: qk.magis });
    void qc.invalidateQueries({ queryKey: qk.magic });
  };
}

// -- MCP servers ------------------------------------------------------------

export type McpServerRow = {
  name: string;
  connection_type: "stdio" | "sse" | "streamable_http";
  command: string | null;
  args: string[];
  url: string | null;
  enabled: boolean;
  connect_timeout: number | null;
  execute_timeout: number | null;
  sse_read_timeout: number | null;
  env: Record<string, string>;
  env_set: Record<string, boolean>;
  headers: Record<string, string>;
  headers_set: Record<string, boolean>;
  created_at: string;
  updated_at: string;
};

export function useMcpServers() {
  return useQuery({
    queryKey: qk.mcpServers,
    queryFn: () => apiFetch<McpServerRow[]>("/api/mcp-servers"),
  });
}

/** Live tool list for one MCP server, fetched on demand.
 *  Independent of the process-wide ``_mcp_tools_cache``
 *  so the Knowledge → MCP detail panel can show fresh
 *  tools before the next chat-turn reload kicks in. */
export type McpServerToolRow = {
  name: string;
  description: string;
  prop_count: number;
};
export function useMcpServerTools(name: string | null) {
  return useQuery({
    queryKey: name ? [...qk.mcpServers, name, "tools"] as const : ["mcp-server-tools", "none"],
    queryFn: () =>
      apiFetch<{ name: string; items: McpServerToolRow[]; total: number }>(
        `/api/mcp-servers/${encodeURIComponent(name as string)}/tools`,
      ),
    enabled: !!name,
    // MCP config is operator-edited and rarely changes.
    // 5 min of freshness means a typical "open Knowledge
    // → expand minimax → edit a tool's env → expand
    // again" round-trip hits the cache (the mutation
    // hook invalidates ``qk.mcpServers`` on success,
    // which is a prefix of this query key, so the
    // operator's edit still refetches).
    staleTime: 5 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  });
}

export type McpServerIn = {
  name: string;
  connection_type: "stdio" | "sse" | "streamable_http";
  command: string | null;
  args: string[];
  url: string | null;
  enabled: boolean;
  connect_timeout: number | null;
  execute_timeout: number | null;
  sse_read_timeout: number | null;
  env: Record<string, string>;
  headers: Record<string, string>;
};

/** POST /api/mcp-servers. */
export function useCreateMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: McpServerIn) =>
      apiFetch<McpServerRow>("/api/mcp-servers", {
        method: "POST",
        body: payload,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.mcpServers });
    },
  });
}

/** PATCH /api/mcp-servers/{name}. */
export function useUpdateMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      payload,
    }: {
      name: string;
      payload: McpServerIn;
    }) =>
      apiFetch<McpServerRow>(`/api/mcp-servers/${encodeURIComponent(name)}`, {
        method: "PATCH",
        body: payload,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.mcpServers });
    },
  });
}

/** DELETE /api/mcp-servers/{name}. */
export function useDeleteMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      apiFetch<void>(
        `/api/mcp-servers/${encodeURIComponent(name)}`,
        { method: "DELETE" },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.mcpServers });
    },
  });
}

/** POST /api/mcp-servers/{name}/toggle. */
export function useToggleMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      apiFetch<McpServerRow>(
        `/api/mcp-servers/${encodeURIComponent(name)}/toggle`,
        { method: "POST" },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.mcpServers });
    },
  });
}

// ────────────────────────────────────────────────────────────────── //
// Migration: raw fetch() → react-query.
// Hooks added in the same file so every consumer (App / settings /
// chat / onboarding) can dedup on the same ``qk.*`` keys without
// hunting for a separate `mutations.ts`. Single source of truth.
// ────────────────────────────────────────────────────────────────── //

// -- auth / boot -----------------------------------------------------------

export type MeRow = {
  contact_id: number;
  magis_admin_id: number | null;
  tgid: string | null;
  display_name: string | null;
  // Administrator authority is MAGIS-scoped; contact_id is only the
  // selected runtime's local projection / data owner.
  admin: boolean;
  assigned?: boolean;
  selected_magi_id: number;
  two_factor_enabled: boolean;
  authentication_mode: "local_no_2fa" | "im_2fa_enabled";
};

/** GET /api/auth/me — the boot identity check. */
export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: () => apiFetch<MeRow>("/api/auth/me"),
    retry: false, // 401 must propagate to drive the routing decision
    // Once /me has 401'd we know the browser has no session, and
    // nothing about window focus changes that — the login flow
    // invalidates ``qk.me`` explicitly on success. Without this
    // guard the global ``refetchOnWindowFocus`` re-probes on every
    // tab switch, so a logged-out operator accumulates one console
    // 401 per focus event. Keep refetching once signed in, so a
    // session that expires in the background is still noticed.
    refetchOnWindowFocus: (query) => query.state.status !== "error",
  });
}

export type AvailableMAGI = { id: number; name: string | null };
export function useAvailableMagi() {
  return useQuery({
    queryKey: qk.availableMagi,
    queryFn: () => apiFetch<{ magi: AvailableMAGI[] }>("/api/auth/available-magi"),
  });
}

export type TargetLoginAccount = {
  // Runtime-local contacts.id — primary key. Same contact
  // can appear twice (once per role: admin + assigned) so
  // the operator picks which layer they want to log in as.
  contact_id: number;
  role: "admin" | "assigned";
  name: string;
  admin: boolean;
  assigned: boolean;
  has_tg_code: boolean;
  tgid: number | null;
  auth_mode: "local_no_2fa" | "im_2fa_enabled" | "recovery_local_no_2fa" | "disabled";
  local_direct_allowed: boolean;
};
export function useTargetLoginAccounts(magiId: number | null) {
  return useQuery({
    queryKey: qk.targetAccounts(magiId ?? 0),
    queryFn: () => apiFetch<{ accounts: TargetLoginAccount[] }>(`/api/auth/targets/${magiId}/accounts`),
    enabled: magiId !== null,
  });
}

export function useSendTargetLoginCode(magiId: number) {
  return useMutation({
    mutationFn: (vars: { contact_id: number; role: "admin" | "assigned" }) =>
      apiFetch<{ ok: boolean; expires_in?: number; error?: string }>(
        `/api/auth/targets/${magiId}/send-login-code`,
        { method: "POST", body: vars },
      ),
  });
}

export function useVerifyTargetLoginCode(magiId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { contact_id: number; role: "admin" | "assigned"; code: string }) =>
      apiFetch<{ ok: boolean; error?: string }>(
        `/api/auth/targets/${magiId}/verify-login-code`, { method: "POST", body: payload },
      ),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: qk.me }); },
  });
}

export function useLocalDirectLogin(magiId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { contact_id: number; role: "admin" | "assigned" }) =>
      apiFetch<{ ok: boolean; error?: string }>(
        `/api/auth/targets/${magiId}/local-direct-login`,
        { method: "POST", body: payload },
      ),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: qk.me }); },
  });
}

/** POST /api/auth/logout — invalidates the boot session. */
export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch<void>("/api/auth/logout", { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.me });
    },
  });
}

export function useVerifyBot() {
  return useMutation({
    mutationFn: (token: string) =>
      apiFetch<{ ok: boolean; username?: string; error?: string }>(
        "/api/control/telegram/verify",
        { method: "POST", body: { token } },
      ),
  });
}

export function useSaveBot() {
  return useMutation({
    mutationFn: (payload: { token: string; username: string }) =>
      apiFetch<{ ok: boolean; error?: string }>("/api/control/telegram/bootstrap", {
        method: "POST",
        body: payload,
      }),
    onSuccess: () => undefined,
  });
}

// -- system settings: tool loop + compaction ------------------------------

export type ToolMaxIterations = {
  current: number;
  default: number;
  min: number;
  max: number;
};

export function useToolMaxIterations() {
  return useQuery({
    queryKey: qk.systemSettings("tool-max-iterations"),
    queryFn: () =>
      apiFetch<ToolMaxIterations>("/api/system-settings/tool-max-iterations"),
  });
}

export function useUpdateToolMaxIterations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (value: number) =>
      apiFetch<ToolMaxIterations>("/api/system-settings/tool-max-iterations", {
        method: "PUT",
        body: { value },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: qk.systemSettings("tool-max-iterations"),
      });
    },
  });
}

export type CompactConfig = {
  context_window: number;
  threshold_pct: number;
  keep_recent: number;
  default_context_window: number;
  default_threshold_pct: number;
  default_keep_recent: number;
};

export function useCompactConfig() {
  return useQuery({
    queryKey: qk.systemSettings("compact-config"),
    queryFn: () =>
      apiFetch<CompactConfig>("/api/system-settings/compact-config"),
  });
}

export function useUpdateCompactConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      context_window: number;
      threshold_pct: number;
      keep_recent: number;
    }) =>
      apiFetch<CompactConfig>("/api/system-settings/compact-config", {
        method: "PUT",
        body: payload,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: qk.systemSettings("compact-config"),
      });
    },
  });
}

// -- TG reaction picker (with optimistic update) --------------------------

export type TgReaction = {
  current: string;
  default: string;
  choices: { value: string }[];
};

export function useTgReaction(kind: "read" | "done") {
  return useQuery({
    queryKey: qk.tgReaction(kind),
    queryFn: () =>
      apiFetch<TgReaction>(`/api/tg-settings/${kind}-reaction`),
  });
}

export function useUpdateTgReaction(kind: "read" | "done") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (emoji: string) =>
      apiFetch<TgReaction>(`/api/tg-settings/${kind}-reaction`, {
        method: "PUT",
        body: { emoji },
      }),
    // Optimistic update: the operator's pick should
    // flip the pill immediately, and roll back if the
    // server rejects it. onSettled always invalidates
    // so the cache is reconciled to whatever the server
    // says.
    onMutate: async (emoji: string) => {
      await qc.cancelQueries({ queryKey: qk.tgReaction(kind) });
      const previous = qc.getQueryData<TgReaction>(qk.tgReaction(kind));
      if (previous) {
        qc.setQueryData<TgReaction>(qk.tgReaction(kind), {
          ...previous,
          current: emoji,
        });
      }
      return { previous };
    },
    onError: (
      _err: unknown,
      _vars: string,
      ctx: { previous?: TgReaction } | undefined,
    ) => {
      if (ctx?.previous) {
        qc.setQueryData(qk.tgReaction(kind), ctx.previous);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.tgReaction(kind) });
    },
  });
}

// -- soul / persona --------------------------------------------------------

export type SoulData = {
  content: string;
  modified_at: string | null;
  is_bundled_fallback: boolean;
};

export function useSoul() {
  return useQuery({
    queryKey: qk.soul,
    queryFn: () => apiFetch<SoulData>("/api/soul"),
  });
}

export function useUpdateSoul() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (content: string) =>
      apiFetch<{ modified_at: string }>("/api/soul", {
        method: "PUT",
        body: { content },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.soul });
    },
  });
}

export function useResetSoul() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch<{ modified_at: string }>("/api/soul/reset", {
        method: "POST",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.soul });
    },
  });
}

// -- chat: search + session + tasks ---------------------------------------

export type ChatSearchResult = {
  q: string;
  uid: number;
  items: Array<{
    conversation_id: number;
    message_id: number;
    role: string;
    ts: string;
    snippet: string;
    title: string | null;
    score: number;
    deliveryAddress: string;
    channel: string;
  }>;
  total: number;
  limit: number;
  offset: number;
};

/**
 * Search chat history. The component is responsible for
 * debouncing the query string — the hook only fires when
 * ``q`` is non-empty. ``placeholderData: keepPrevious``
 * keeps the last result visible while a new search is in
 * flight so the list doesn't blink on every keystroke.
 */
export function useChatSearch(q: string) {
  return useQuery({
    queryKey: qk.chatSearch(q),
    queryFn: () => {
      const params = new URLSearchParams({ q, limit: "20" });
      return apiFetch<ChatSearchResult>(
        `/api/chat/search?${params.toString()}`,
      );
    },
    enabled: q.trim().length > 0,
    placeholderData: (prev) => prev,
  });
}

export type ChatConversationList = {
  items: Array<{
    conversation_id: number;
    created_at: string;
    updated_at: string;
    title: string | null;
    channel: string;
  }>;
  total: number;
  limit: number;
  offset: number;
};

/**
 * Browse-mode conversations list. Paginated; the caller is
 * responsible for accumulating ``items`` across pages
 * (react-query fetches one page at a time).
 */
export function useChatConversations(opts: { limit?: number; offset?: number; enabled?: boolean } = {}) {
  const { limit = 20, offset = 0, enabled = true } = opts;
  return useQuery({
    queryKey: qk.chatConversations(limit, offset),
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      return apiFetch<ChatConversationList>(`/api/chat/conversations?${params.toString()}`);
    },
    enabled,
  });
}

export type ChatConversationOut = {
  conversation_id: number;
  uid: number;
  channel: string;
  delivery_address: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  messages: Array<{
    message_id: number;
    role: "user" | "assistant";
    ts: string;
    text: string;
  }>;
};

/** GET /api/chat/conversations/{id} — full conversation incl. messages. */
export function useChatConversation(conversationId: number | null) {
  return useQuery({
    queryKey: conversationId !== null ? qk.chatConversation(conversationId) : ["chatConversation", "none"],
    queryFn: () =>
      apiFetch<ChatConversationOut>(
        `/api/chat/conversations/${conversationId as number}`,
      ),
    enabled: conversationId !== null,
  });
}

// -- tasks (per-item + mutations) -----------------------------------------

export type TaskOut = {
  id: number;
  task_id: string;
  name: string;
  prompt: string;
  cron: string;
  run_at: string | null;
  delivery_to: string | null;
  tz: string;
  target_channel: "webui" | "tg";
  uid: number;
  enabled: boolean;
  consecutive_failures: number;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  description?: string | null;
  conversation_id?: number | null;
};

export function useTask(taskId: string | null) {
  return useQuery({
    queryKey: taskId ? qk.task(taskId) : ["task", "none"],
    queryFn: () =>
      apiFetch<TaskOut>(
        `/api/tasks/${encodeURIComponent(taskId as string)}`,
      ),
    enabled: !!taskId,
  });
}

export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: unknown) =>
      apiFetch<TaskOut>("/api/tasks", { method: "POST", body: payload }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

export function useUpdateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      taskId,
      payload,
    }: {
      taskId: string;
      payload: unknown;
    }) =>
      apiFetch<TaskOut>(
        `/api/tasks/${encodeURIComponent(taskId)}`,
        { method: "PATCH", body: payload },
      ),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: qk.task(vars.taskId) });
    },
  });
}

export function useDeleteTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      apiFetch<void>(`/api/tasks/${encodeURIComponent(taskId)}`, {
        method: "DELETE",
      }),
    onSuccess: (_data, taskId) => {
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.removeQueries({ queryKey: qk.task(taskId) });
    },
  });
}

export function useRunTaskNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      apiFetch<{ job_id: number }>(
        `/api/tasks/${encodeURIComponent(taskId)}/run`,
        { method: "POST" },
      ),
    onSuccess: (_data, taskId) => {
      void qc.invalidateQueries({ queryKey: qk.taskRuns(taskId) });
      void qc.invalidateQueries({ queryKey: qk.task(taskId) });
    },
  });
}

export function useToggleTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      apiFetch<TaskOut>(`/api/tasks/${encodeURIComponent(taskId)}`, {
        method: "PATCH",
        body: { enabled: undefined as unknown as boolean },
      }),
    // The PATCH body is built in the calling code so the
    // mutation can take just the id; the caller passes
    // the new ``enabled`` value via the second arg.
    // We use a tuple shape to keep the hook signature
    // simple.
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}
