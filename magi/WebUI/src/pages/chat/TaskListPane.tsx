/**
 * TaskListPane — operator-facing CRUD over scheduled tasks.
 *
 * v2 layout (preset + moment, no raw cron, no per-task
 * timezone picker, no per-task credential picker):
 *
 *   - Header row + “+ 新建” → opens the TaskFormDrawer.
 *   - Filter chips: all / enabled / disabled.
 *   - Table columns: name / channel / last status /
 *     last_run_at / actions.
 *   - Each row: 「立刻跑」 / 启用/停用 / 「编辑」 / 「删除」.
 *
 * The drawer asks for FOUR form fields:
 *
 *   - 名称 (label)            — short, 120 chars max
 *   - 触发方式 (frequency)    — Hourly / Daily / Weekly /
 *                                Monthly dropdown. (Once
 *                                is supported by the
 *                                backend via the LLM tool
 *                                path; the WebUI drawer
 *                                stays 4-preset for v0 —
 *                                use “立刻跑” for one-off
 *                                firing.)
 *   - 时间 (moment)            — depends on frequency:
 *                                  Hourly  → 分钟 (0-59)
 *                                  Daily   → HH:MM
 *                                  Weekly  → 星期 (Mon..Sun)
 *                                            + HH:MM
 *                                  Monthly → 几日 (1-31) + HH:MM
 *   - Channel                 — webui / tg
 *
 * The schedule cell renders a humanised phrase
 * (see :func:`cronHumanize.humanizeCron` / :func:`humanizeRunAt`)
 * instead of the raw cron; the raw value still ships in
 * the API response and is the cell's ``title=`` for
 * inspection. ``title`` style is the operator's
 * escape hatch — hover any cell to see the underlying
 * cron / ISO datetime verbatim.
 *
 * Credentials and timezone are NOT asked. Credentials
 * are bound implicitly to whoever is signed in (admin
 * or assigned contact — the backend's role gate
 * refuses other roles); the timezone is read from the
 * Settings panel's ``system.timezone`` field globally.
 */
import { useEffect, useState } from "react";

import { TaskFormDrawer } from "./TaskFormDrawer";
import { RunsHistoryDrawer } from "./RunsHistoryDrawer";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, qk } from "../../lib/queryClient";
import { humanizeCron, humanizeRunAt } from "./cronHumanize";


// Human-readable timestamp formatter for the runs-history
// drawer + any other operator-facing cell. ISO strings
// from the backend ("2026-07-22T00:14:32.923580+00:00")
// are unreadable for the operator; we render the local
// wall-clock (the browser's timezone, which matches the
// operator's machine) plus a "X minutes ago" relative
// hint when the run is recent.
//
// Implementation notes:
// - We parse the ISO via Date(); the string carries a
//   +00:00 offset so the result is the absolute instant.
// - ``Intl.DateTimeFormat`` formats in the browser's
//   local timezone, which is what the operator expects.
// - The relative clause falls back to the absolute time
//   when the run is older than a week (avoids the
//   "47 days ago" clutter that doesn't help anyone).
export function formatRunTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;  // unparseable — fall back to the raw string
  const now = Date.now();
  const diff = now - ms;
  // Absolute: e.g. "2026-07-22 00:14:32" (no UTC suffix;
  // the browser's local TZ is implicit).
  const abs = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ms));
  // Relative for fresh runs; skip for old ones.
  if (diff < 0) return abs;  // future (clock skew) — show absolute only
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${abs} · ${sec} 秒前`;
  if (sec < 3600) return `${abs} · ${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${abs} · ${Math.floor(sec / 3600)} 小时前`;
  if (sec < 7 * 86400) return `${abs} · ${Math.floor(sec / 86400)} 天前`;
  return abs;
}

export type TaskRow = {
  id: string;
  name: string;
  prompt: string;
  cron: string;
  // ``run_at`` carries the ISO timestamp for ``frequency="once"``
  // tasks. Mutually exclusive with ``cron`` in the row — see
  // the cell render below.
  run_at: string | null;
  // ``delivery_to`` is the concrete destination: the
  // operator's bound per-channel delivery address (TG
  // chat id today), ``"new"`` for fresh-session webui
  // fires, an explicit
  // chat session_id, or null (operator-bound fallback at
  // fire time). The cell renders a "→ <target>" snippet
  // below the schedule row so the operator can audit the
  // delivery site at a glance.
  delivery_to: string | null;
  tz: string;
  channel: "webui" | "tg";
  uid: number;
  enabled: boolean;
  consecutive_failures: number;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  // ``session_id`` points at the agent's home session
  // (allocated at task creation, channel="task"). The
  // runs drawer fetches the session's chat history
  // directly via this id. Nullable for legacy rows
  // created before the column landed; the runner
  // backfills on first fire.
  session_id: string | null;
};

// One row of the ``/api/tasks/{id}/runs`` response — used
// by the run-now polling loop to detect when a fire settles
// into ``success`` / ``failed``. The runner writes
// ``status="running"`` first, then transitions to a terminal
// state; the loop bails when our ``run_id`` is terminal.
export type TaskRunRow = {
  id: string;
  task_id: string;
  session_id: string | null;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  latency_ms: number | null;
  status: string;
  error: string | null;
  reply_excerpt: string | null;
};

export type Frequency = "hourly" | "daily" | "weekly" | "monthly" | "once";
type Filter = "all" | "enabled" | "disabled";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  return apiFetch<T>(`/api/tasks${path}`, init);
}

export const WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export default function TaskListPane() {
  const [filter, setFilter] = useState<Filter>("all");
  const queryClient = useQueryClient();
  const tasksQuery = useTasks(filter);
  const rows = tasksQuery.data ?? null;
  const loadError =
    tasksQuery.error instanceof Error
      ? tasksQuery.error.message
      : tasksQuery.isError
        ? "Network error"
        : null;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  // ``runsForId`` is the task currently showing the runs-
  // history drawer. Clicking a row's name opens the
  // drawer for that task; clicking the close button or
  // pressing Escape clears it. The drawer's data comes
  // from ``GET /api/tasks/{id}/runs`` (already pinned
  // by the backend) and shows every fire's terminal
  // status, error summary, and reply excerpt.
  const [runsForId, setRunsForId] = useState<string | null>(null);
  const [systemTz, setSystemTz] = useState<string | null>(null);
  // ``runningTaskIds`` carries the task_id → run_id mapping
  // for in-flight manual fires. The row's status cell
  // renders a spinner while the id is here; a polling
  // effect watches /api/tasks/{id}/runs and evicts the
  // entry once the run settles into success / failed.
  // Map (not Set) so the polling loop can match the exact
  // ``run_id`` the API returned — keeps a stale run from
  // a previous click from satisfying the new one.
  const [runningTasks, setRunningTasks] = useState<
    Map<string, string>
  >(() => new Map());

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: qk.tasks() });
  }

  // Fetch the system-wide tz once so the page header
  // can show "所有任务按 <tz> 调度". A change requires
  // a page reload — same expectation as the rest of
  // Settings; in v0 we don't ship real-time sync for
  // the simple dashboard view.
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/system-settings/timezone", {
          credentials: "include",
        });
        if (r.ok) {
          const body = (await r.json()) as {
            current: string;
            default: string;
          };
          setSystemTz(body.current || body.default || "UTC");
        }
      } catch {
        /* ignore — header just hides the badge */
      }
    })();
  }, []);



  // Polling loop for in-flight manual fires. While at
  // least one task id is in ``runningTasks``, hit
  // /api/tasks/{id}/runs every 1.5 s and evict any id
  // whose run has reached a terminal status. The loop
  // dies on its own when the map goes empty (no manual
  // runs in flight → no interval needed).
  //
  // We poll per-id rather than /api/tasks so the response
  // payload stays small (a few TaskRun rows vs the full
  // task list). Polling also gives us a free "did it
  // succeed or fail?" signal — we don't have to refetch
  // the entire task list to learn the answer.
  //
  // No auto-open on terminal: the operator pulls the
  // drawer via the row's 「查看日志」 button when they
  // want it. Auto-opening on every fire would steal
  // focus from whatever the operator is currently
  // doing (browsing other tasks, editing form, etc).
  useEffect(() => {
    if (runningTasks.size === 0) return;
    let cancelled = false;
    const tick = async () => {
      for (const [taskId, runId] of runningTasks) {
        try {
          const runs = await api<TaskRunRow[]>(`/${taskId}/runs`);
          const mine = runs.find((r) => r.id === runId);
          // Terminal = success or failed. ``running``
          // (the only other shape the runner writes) means
          // "still in flight; check next tick".
          if (
            mine &&
            (mine.status === "success" || mine.status === "failed")
          ) {
            // Evict this id from the polling set. Use a
            // functional update so a parallel click that
            // re-added the same id with a fresh run_id
            // isn't clobbered.
            setRunningTasks((prev) => {
              if (!prev.has(taskId)) return prev;
              if (prev.get(taskId) !== runId) return prev;
              const next = new Map(prev);
              next.delete(taskId);
              return next;
            });
            // Refresh the task list so the row's
            // ``last_status`` / ``last_run_at`` flip to the
            // fresh values. We only refresh on terminal —
            // mid-run polling doesn't need it.
            await refresh();
          }
        } catch {
          // Polling failures are non-fatal; the next
          // tick will retry. The button itself already
          // surfaced its own error path on click.
        }
      }
    };
    const interval = setInterval(() => {
      if (!cancelled) void tick();
    }, 1500);
    // Fire one immediate tick so a quick success doesn't
    // wait 1.5 s for the first interval.
    void tick();
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // ``refresh`` is intentionally excluded — it captures
    // the latest closure on every render via the
    // component scope, and including it would re-arm the
    // interval on every state change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningTasks]);

  const deleteTaskMut = useMutation({
    mutationFn: (t: TaskRow) =>
      apiFetch<void>(`/api/tasks/${t.id}`, { method: "DELETE" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: qk.tasks() }),
  });
  async function deleteTask(t: TaskRow) {
    if (!confirm(`确定删除任务「${t.name}」？此操作不可撤销。`)) return;
    try {
      await deleteTaskMut.mutateAsync(t);
    } catch {
      // Mutation error surfaces on tasksQuery on next refetch.
    }
  }

  const runNowMut = useMutation({
    mutationFn: (t: TaskRow) =>
      apiFetch<{ run_id: string }>(`/api/tasks/${t.id}/run`, { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: qk.tasks() }),
  });
  async function runNow(t: TaskRow) {
    // Set the spinner IMMEDIATELY — on the same frame as
    // the click — so the operator sees feedback before
    // the POST round-trips. The API hasn't returned the
    // ``run_id`` yet, so we use a sentinel
    // ``"__pending__"`` and update the map once the
    // response lands.
    //
    // The polling loop handles the sentinel correctly:
    // it tries to look up the run by id, fails to find
    // one (the runner hasn't written a row yet), and
    // does nothing. Once we swap in the real run_id the
    // effect re-runs and the next poll finds it.
    setRunningTasks((prev) => {
      const next = new Map(prev);
      next.set(t.id, "__pending__");
      return next;
    });
    let run_id: string;
    try {
      const out = await runNowMut.mutateAsync(t);
      run_id = out.run_id;
    } catch (err) {
      // Roll the optimistic entry back so the spinner
      // doesn't stick if the POST fails.
      setRunningTasks((prev) => {
        if (!prev.has(t.id) || prev.get(t.id) !== "__pending__") {
          return prev;
        }
        const next = new Map(prev);
        next.delete(t.id);
        return next;
      });
      return;
    }
    // Replace the sentinel with the real run_id. The
    // polling effect's dependency on this Map means the
    // effect re-runs and the next poll picks up the real
    // id.
    setRunningTasks((prev) => {
      if (!prev.has(t.id)) return prev;
      const next = new Map(prev);
      next.set(t.id, run_id);
      return next;
    });
    // Refresh the row's columns (last_status,
    // last_run_at, session_id from the runner's
    // backfill) so the table cell values match reality.
    // This is independent of the polling-driven refresh
    // — both run, second one is a no-op for cell values.
    void queryClient.invalidateQueries({ queryKey: qk.tasks() });
  }

  const toggleEnabledMut = useMutation({
    mutationFn: (t: TaskRow) =>
      apiFetch<TaskRow>(`/api/tasks/${t.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !t.enabled }),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: qk.tasks() }),
  });
  async function toggleEnabled(t: TaskRow) {
    try {
      await toggleEnabledMut.mutateAsync(t);
    } catch {
      // Mutation error surfaces on tasksQuery on next refetch.
    }
  }

  return (
    // Same layout pattern as the chat pane: outer flex
    // column with ``h-full min-h-0`` so the page fills
    // its parent column, header pinned at top, table
    // container scrolls. ``min-h-0`` is the critical bit
    // — without it, ``flex-1 overflow-y-auto`` on the
    // table container expands to fit all rows instead of
    // scrolling, and the page out-grows the viewport on
    // long lists.
    <div className="flex flex-col h-full min-h-0 space-y-4">
      <div className="shrink-0 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">定时任务</h2>
          <p className="mt-1 text-sm text-ink-soft">
            按触发方式 + 时间到点跑任务，每次会话独立 — operator 在 chat 历史能看到每一次的回复。
            {systemTz && (
              <span className="ml-2 text-xs text-ink-soft">
                （时区：<span className="font-mono">{systemTz}</span>，去
                <a href="/chat/scheduled-tasks?tab=settings" className="text-sky-700 ml-1">设置</a>
                改）
              </span>
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setEditingId(null);
            setDrawerOpen(true);
          }}
          className="btn btn-primary text-sm py-2 px-4 shrink-0"
        >
          + 新建任务
        </button>
      </div>

      <div className="flex items-center gap-2 text-xs">
        {(["all", "enabled", "disabled"] as Filter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={
              "px-3 py-1 rounded-md border transition " +
              (filter === f
                ? "bg-sky-deep text-white border-sky-deep"
                : "bg-white/60 text-ink-soft border-sky-light/40 hover:text-ink")
            }
          >
            {f === "all" ? "全部" : f === "enabled" ? "已启用" : "已停用"}
          </button>
        ))}
      </div>

      {loadError && <p className="form-error">✗ {loadError}</p>}

      <div className="glass-card overflow-hidden flex-1 min-h-0 flex flex-col">
        {rows === null && !loadError ? (
          <p className="p-6 text-sm text-ink-soft">加载中…</p>
        ) : rows && rows.length === 0 ? (
          <p className="p-6 text-sm text-ink-soft">还没有定时任务。点 + 新建任务 创建第一条。</p>
        ) : rows && rows.length > 0 ? (
          <div className="flex-1 min-h-0 overflow-y-auto">
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">名称</th>
                <th className="py-2 pr-4 font-medium">周期</th>
                <th className="py-2 pr-4 font-medium">Channel</th>
                <th className="py-2 pr-4 font-medium">最近状态</th>
                <th className="py-2 font-medium w-44 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr
                  key={t.id}
                  className={
                    "border-b border-sky-light/30 last:border-0 " +
                    (t.enabled ? "" : "opacity-60")
                  }
                >
                  <td className="py-2 pr-4 text-ink font-medium">
                    <div className="flex items-center gap-2">
                      {/* Click name → runs-history drawer.
                          The drawer shows every fire's
                          status / error / reply excerpt so
                          the operator can see *why* a
                          "成功" row in the table actually
                          didn't push to TG (e.g. bot not
                          registered → reply lives in chat
                          history but ``_tg_send_callback``
                          was never wired). */}
                      <button
                        type="button"
                        onClick={() => setRunsForId(t.id)}
                        title="点击查看运行历史"
                        className="text-left font-medium text-ink hover:text-sky-deep underline-offset-2 hover:underline cursor-pointer"
                      >
                        {t.name}
                      </button>
                      {/* Pencil icon — points to the LEFT
                          so it visually "edits the name
                          to its left" (the convention most
                          editor toolbars use; U+270E ✎ and
                          most emoji pencils point right/down,
                          which reads as "the next thing over
                          there is what gets edited" — wrong
                          direction here). Inline SVG so the
                          orientation is consistent across
                          font fallbacks — emoji-rendering
                          platforms vary and a flipped glyph
                          via CSS scaleX isn't reliable on
                          every browser either. */}
                      <button
                        type="button"
                        onClick={() => {
                          setEditingId(t.id);
                          setDrawerOpen(true);
                        }}
                        title="编辑任务"
                        aria-label="编辑任务"
                        className="w-7 h-7 inline-flex items-center justify-center rounded-md text-ink-soft hover:text-sky-deep hover:bg-sky-pale/40 transition"
                      >
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 16 16"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          aria-hidden="true"
                        >
                          {/* pencil body angled top-right →
                              bottom-left, tip touching the
                              name bubble on the left */}
                          <path d="M11 3 L13 5 L5.5 12.5 L3 13 L3.5 10.5 Z" />
                          {/* eraser end (top-right) */}
                          <path d="M11 3 L13 5 L14 4 L12 2 Z" />
                          {/* tip emphasis */}
                          <path d="M3.5 10.5 L5.5 12.5" />
                        </svg>
                      </button>
                      {t.consecutive_failures > 0 && (
                        <span className="text-[10px] text-amber-700">
                          ⚠ 已失败 {t.consecutive_failures} 次
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs">
                    {/*
                      Schedule cell — show the humanised
                      rendering, not the raw cron. The
                      cell picks the branch off the row
                      shape (run_at set → once) rather
                      than the cron field alone, so an
                      old row with both populated still
                      renders sensibly (run_at wins).
                      The delivery destination lives in
                      the next column (Channel) — the
                      two are independent concepts and
                      pairing them read better there.
                    */}
                    {t.run_at ? (
                      <span title={t.run_at}>
                        一次性 · {humanizeRunAt(t.run_at)}
                      </span>
                    ) : (
                      <span title={t.cron}>{humanizeCron(t.cron)}</span>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs">
                    {/*
                      Channel cell — single line. The
                      channel name is implicit in the
                      delivery phrasing (``Telegram →``
                      vs ``新会话``), so no separate
                      label row. ``"new"`` is the magic
                      webui token; explicit session_id /
                      the bound chat id are rendered
                      verbatim.
                      ``null`` means the runner falls back
                      to operator-binding at fire time —
                      we surface that as "(未指定)" rather
                      than a misleading empty cell.
                    */}
                    <div
                      title={
                        t.delivery_to === null
                          ? "未指定 — operator 绑定"
                          : t.delivery_to
                      }
                    >
                      {t.channel === "tg"
                        ? `Telegram → ${
                            t.delivery_to === null
                              ? "(未指定)"
                              : t.delivery_to
                          }`
                        : t.delivery_to === null
                          ? "webui (未指定)"
                          : t.delivery_to === "new"
                            ? "新会话"
                            : t.delivery_to}
                    </div>
                  </td>
                  <td className="py-2 pr-4 text-xs">
                    {runningTasks.has(t.id) ? (
                      // Spinner: the polling loop above
                      // owns the eviction, so we only
                      // render this branch while the
                      // task is in our optimistic set.
                      // The row stays put during the
                      // fire; status flips on the
                      // terminal tick.
                      <span className="inline-flex items-center gap-1.5 text-sky-700">
                        <span className="inline-block h-3 w-3 rounded-full border-2 border-sky-300 border-t-sky-700 animate-spin" />
                        执行中…
                      </span>
                    ) : t.last_status ? (
                      <span
                        className={
                          t.last_status === "success"
                            ? "text-emerald-700"
                            : t.last_status === "failed"
                              ? "text-rose-700"
                              : "text-ink-soft"
                        }
                      >
                        {t.last_status === "success"
                          ? "✓ 成功"
                          : t.last_status === "failed"
                            ? "✗ 失败"
                            : t.last_status}
                      </span>
                    ) : (
                      <span className="text-ink-soft">—</span>
                    )}
                    {t.last_error && (
                      <p className="text-[10px] text-rose-700 mt-0.5 truncate max-w-[200px]" title={t.last_error}>
                        {t.last_error}
                      </p>
                    )}
                  </td>
                  <td className="py-2 text-right">
                    <div className="flex items-center justify-end gap-1 text-sm">
                      {/* All action buttons share the same
                          28×28 hit target so the row reads
                          as a uniform toolbar rather than a
                          pile of differently-sized icons. */}
                      <button
                        type="button"
                        onClick={() => runNow(t)}
                        disabled={!t.enabled}
                        title="立刻跑"
                        aria-label="立刻跑"
                        className="w-7 h-7 inline-flex items-center justify-center rounded-md text-sky-700 hover:text-sky-deep hover:bg-sky-pale/40 transition disabled:text-sky-light/50 disabled:cursor-not-allowed"
                      >
                        ▶
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleEnabled(t)}
                        title={t.enabled ? "停用" : "启用"}
                        aria-label={t.enabled ? "停用" : "启用"}
                        className="w-7 h-7 inline-flex items-center justify-center rounded-md text-sky-700 hover:text-sky-deep hover:bg-sky-pale/40 transition"
                      >
                        {t.enabled ? "⏸" : "▶▶"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setRunsForId(t.id)}
                        title="查看日志"
                        aria-label="查看日志"
                        className="w-7 h-7 inline-flex items-center justify-center rounded-md text-sky-700 hover:text-sky-deep hover:bg-sky-pale/40 transition"
                      >
                        💬
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteTask(t)}
                        title="删除"
                        aria-label="删除"
                        className="w-7 h-7 inline-flex items-center justify-center rounded-md text-rose-700 hover:text-rose-900 hover:bg-rose-50 transition"
                      >
                        {/* Trash icon — U+1F5D1 falls
                            back to a font glyph on most
                            systems; we pair it with the
                            rose tint so the destructive
                            intent reads even before the
                            user hovers the tooltip. */}
                        🗑
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        ) : null}
      </div>

      {drawerOpen && (
        <TaskFormDrawer
          taskId={editingId}
          onClose={() => setDrawerOpen(false)}
          onSaved={() => {
            setDrawerOpen(false);
            void queryClient.invalidateQueries({ queryKey: qk.tasks() });
          }}
        />
      )}

      {runsForId && (() => {
        // Resolve the task's session_id (allocated at
        // task creation) — the drawer's chat-style view
        // fetches the session messages directly. Tasks
        // created by legacy flows may still have null
        // session_id (the runner backfills on first
        // fire); we fall back to the task-id mode for
        // those until the first fire happens.
        const t = rows?.find((row) => row.id === runsForId);
        if (!t) return null;
        return (
          <RunsHistoryDrawer
            taskName={t.name}
            taskId={t.id}
            sessionId={t.session_id ?? null}
            onClose={() => setRunsForId(null)}
          />
        );
      })()}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────── #
// Drawer
// ──────────────────────────────────────────────────────────────────────── #

function useTasks(filter: Filter) {
  return useQuery({
    queryKey: [...qk.tasks(), filter] as const,
    queryFn: () => {
      const params = new URLSearchParams();
      if (filter !== "all") {
        params.set("enabled", filter === "enabled" ? "true" : "false");
      }
      const qs = params.toString();
      return apiFetch<TaskRow[]>(`/api/tasks${qs ? "?" + qs : ""}`);
    },
  });
}
