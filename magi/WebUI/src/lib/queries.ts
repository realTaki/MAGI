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
  id: string; name: string; prompt: string;
  target_channel: "webui" | "tg";
  delivery_to: string | null; enabled: boolean;
  session_id: string | null;
  last_run_at: string | null; last_status: string | null;
  consecutive_failures: number;
  created_at: string; updated_at: string;
  // Preset back-pointers (server-side, read-only on the
  // per-user row). ``preset_key IS NOT NULL`` drives the
  // Knowledge → Tasks pane's "预设任务" / "自定义任务"
  // split.
  preset_id: string | null;
  preset_key: string | null;
};

/** Tasks query — drives the WebUI's two-list layout.
 *
 * ``filter.kind="preset"`` filters to rows where the
 * source template's ``preset_key`` is set (auto-seeded);
 * ``filter.kind="custom"`` filters to operator-authored
 * rows (preset_key IS NULL); omitted → all rows.
 *
 * ``filter.enabled`` narrows further to enabled / disabled.
 * The two axes are independent — a caller can ask
 * "all enabled preset tasks" via
 * ``{ kind: "preset", enabled: true }``.
 */
export function useTasks(filter?: { enabled?: boolean; kind?: "preset" | "custom" }) {
  return useQuery({
    queryKey: qk.tasks(filter),
    queryFn: () => {
      const params = new URLSearchParams();
      if (filter?.enabled !== undefined) {
        params.set("enabled", String(filter.enabled));
      }
      if (filter?.kind) {
        params.set("kind", filter.kind);
      }
      const qs = params.toString();
      return apiFetch<TaskOut[]>(`/api/tasks${qs ? "?" + qs : ""}`);
    },
  });
}

export type TaskRunRow = {
  id: string; task_id: string; session_id: string | null;
  trigger: string; started_at: string; finished_at: string | null;
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
  uid: number;
  telegram_id: string;
  display_name: string | null;
  // ``admin`` is sourced from the contact row (replaces the
  // pre-2024 ``is_super_admin`` boolean that was hardcoded
  // ``true`` for every signed-in user). The frontend uses
  // it to render role-aware UI (e.g. hide operator-only
  // controls for non-admin sign-ins).
  admin: boolean;
};

/** GET /api/auth/me — the boot identity check. */
export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: () => apiFetch<MeRow>("/api/auth/me"),
    retry: false, // 401 must propagate to drive the routing decision
  });
}

/** GET /api/auth/allowed-accounts — login dropdown. */
export type AllowedAccount = { uid: number; name: string };
export function useAllowedAccounts() {
  return useQuery({
    queryKey: qk.allowedAccounts,
    queryFn: () =>
      apiFetch<{ accounts: AllowedAccount[] }>("/api/auth/allowed-accounts"),
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
  telegram_id: number; name: string; admin: boolean; assigned: boolean;
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
    mutationFn: (telegram_id: number) => apiFetch<{ ok: boolean; expires_in?: number; error?: string }>(
      `/api/auth/targets/${magiId}/send-login-code`, { method: "POST", body: { telegram_id } },
    ),
  });
}

export function useVerifyTargetLoginCode(magiId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { telegram_id: number; code: string }) => apiFetch<{ ok: boolean; error?: string }>(
      `/api/auth/targets/${magiId}/verify-login-code`, { method: "POST", body: payload },
    ),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: qk.me }); },
  });
}

export type OnboardingStatus = {
  bot_saved: boolean;
  bot_username?: string;
  super_admins_count: number;
  super_admins: string[];
  onboarding_complete: boolean;
  // ``login_methods`` / ``mode`` mirror the new WebUI-only
  // onboarding path. The wizard uses them to decide whether
  // to resume on the "with TG" or "WebUI only" branch.
  login_methods?: string[];
  mode?: "with_tg" | "webui_only" | null;
};

/** GET /api/onboarding/status — shared between App boot and OnboardingPage. */
export function useOnboardingStatus(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: qk.onboardingStatus,
    queryFn: () => apiFetch<OnboardingStatus>("/api/onboarding/status"),
    enabled: opts?.enabled ?? true,
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
      void qc.invalidateQueries({ queryKey: qk.onboardingStatus });
    },
  });
}

/** POST /api/onboarding/complete — wizard "OK, got it" button. */
export function useCompleteOnboarding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch<{ ok: boolean }>("/api/onboarding/complete", {
        method: "POST",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.onboardingStatus });
    },
  });
}

/** POST /api/onboarding/restart — dashboard "Restart onboarding". */
export function useRestartOnboarding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch<{ ok: boolean }>("/api/onboarding/restart", {
        method: "POST",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.onboardingStatus });
      void qc.invalidateQueries({ queryKey: qk.me });
    },
  });
}

// -- onboarding wizard (per-step mutations) -------------------------------

export function useVerifyBot() {
  return useMutation({
    mutationFn: (token: string) =>
      apiFetch<{ ok: boolean; username?: string; error?: string }>(
        "/api/onboarding/verify-bot",
        { method: "POST", body: { token } },
      ),
  });
}

export function useSaveBot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { token: string; username: string }) =>
      apiFetch<{ ok: boolean; error?: string }>("/api/onboarding/save-bot", {
        method: "POST",
        body: payload,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.onboardingStatus });
    },
  });
}

export function useSendAdminCode() {
  return useMutation({
    mutationFn: (tgid: string) =>
      apiFetch<{ ok: boolean; expires_in?: number; error?: string }>(
        "/api/onboarding/send-admin-code",
        { method: "POST", body: { tgid } },
      ),
  });
}

// WebUI-only onboarding step 2 — write the first admin
// row + a password credential in one shot. Used when
// the wizard's first step picked "WebUI only" instead of
// "Telegram". The response carries the new admin uid so
// the wizard's Step3 summary can render the real name.
export function useSetAdminPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { name: string; password: string }) =>
      apiFetch<{ ok: boolean; error?: string; admin_uid?: number | null }>(
        "/api/onboarding/set-admin-password",
        { method: "POST", body: payload },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.onboardingStatus });
    },
  });
}

export function useVerifyAdminCode() {
  return useMutation({
    mutationFn: (payload: { tgid: string; code: string }) =>
      apiFetch<{
        ok: boolean;
        display_name?: string;
        error?: string;
      }>("/api/onboarding/verify-admin-code", {
        method: "POST",
        body: payload,
      }),
  });
}

export function useSaveAdmin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tgids: string[]) =>
      apiFetch<{ ok: boolean; count?: number; error?: string }>(
        "/api/onboarding/save-admin",
        { method: "POST", body: { tgids } },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.onboardingStatus });
    },
  });
}

// -- auth login flow -------------------------------------------------------

export function useSendLoginCode() {
  return useMutation({
    mutationFn: (uid: number) =>
      apiFetch<{ ok: boolean; expires_in?: number; error?: string }>(
        "/api/auth/send-login-code",
        { method: "POST", body: { uid } },
      ),
  });
}

export function useVerifyLoginCode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { uid: number; code: string }) =>
      apiFetch<{ ok: boolean; error?: string }>(
        "/api/auth/verify-login-code",
        { method: "POST", body: payload },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.me });
      void qc.invalidateQueries({ queryKey: qk.allowedAccounts });
    },
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
    session_id: string;
    message_id: string;
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

export type ChatSessionList = {
  items: Array<{
    session_id: string;
    created_at: string;
    created_by_uid: number;
    updated_at: string;
    message_count: number;
    preview: string;
    title: string | null;
  }>;
  total: number;
  limit: number;
  offset: number;
};

/**
 * Browse-mode sessions list. Paginated; the caller is
 * responsible for accumulating ``items`` across pages
 * (react-query fetches one page at a time).
 */
export function useChatSessions(opts: { limit?: number; offset?: number; enabled?: boolean } = {}) {
  const { limit = 20, offset = 0, enabled = true } = opts;
  return useQuery({
    queryKey: qk.chatSessions(limit, offset),
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      return apiFetch<ChatSessionList>(`/api/chat/sessions?${params.toString()}`);
    },
    enabled,
  });
}

export type ChatSessionOut = {
  session_id: string;
  uid: number;
  channel: string;
  delivery_address: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  messages: Array<{
    message_id: string;
    role: "user" | "assistant";
    ts: string;
    text: string;
  }>;
};

/** GET /api/chat/sessions/{id} — full session incl. messages. */
export function useChatSession(sessionId: string | null) {
  return useQuery({
    queryKey: sessionId ? qk.chatSession(sessionId) : ["chatSession", "none"],
    queryFn: () =>
      apiFetch<ChatSessionOut>(
        `/api/chat/sessions/${encodeURIComponent(sessionId as string)}`,
      ),
    enabled: !!sessionId,
  });
}

// -- tasks (per-item + mutations) -----------------------------------------

export type TaskOut = {
  id: string;
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
  session_id?: string | null;
  // Preset back-pointers (read-only on the per-user row).
  // ``preset_key IS NOT NULL`` is the source of truth for
  // the Knowledge → Tasks pane's "preset" / "custom" split;
  // ``preset_id`` is the FK back to ``task_presets.id``
  // (NULL after a template delete — ``preset_key`` stays
  // populated for the snapshot grouping).
  preset_id?: string | null;
  preset_key?: string | null;
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
      apiFetch<{ run_id: string }>(
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

// -- Password login helpers (single-runtime / direct WebUI) --
//
// The control-plane login flow lives in
// ``useTargetLoginAccounts`` / ``useSendTargetLoginCode``
// above — those proxy to a target MAGI runtime. The
// password login endpoints live in the WebUI process —
// they don't go through ``runtime_proxy`` because the
// cookie is created on the WebUI side, not the target.
// The two surfaces co-exist: a control-plane deployment
// can still let the operator choose password (on the
// target runtime) once a target-version of these
// endpoints lands.

export interface LoginMethodsResponse {
  uid: number;
  methods: string[];
  is_webui_only: boolean;
}

export function useLoginMethods(uid: number | null) {
  return useQuery({
    queryKey: ["auth", "login-methods", uid] as const,
    queryFn: () =>
      apiFetch<LoginMethodsResponse>(
        `/api/auth/login-methods?uid=${uid ?? 0}`,
      ),
    enabled: uid != null && uid > 0,
  });
}

export function useLoginPassword() {
  return useMutation({
    mutationFn: (vars: { uid: number; password: string }) =>
      apiFetch<{ ok: boolean; error?: string; retry_after?: number | null }>(
        "/api/auth/login-password",
        {
          method: "POST",
          body: JSON.stringify(vars),
        },
      ),
  });
}
