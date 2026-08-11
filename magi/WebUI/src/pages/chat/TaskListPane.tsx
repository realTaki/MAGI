/**
 * TaskListPane — operator-facing CRUD over scheduled tasks.
 *
 * v3 layout (preset + custom split):
 *
 *   ┌────────────────────────────────────────────────────┐
 *   │ Header: 定时任务 + tz line                         │
 *   ├────────────────────────────────────────────────────┤
 *   │ 自定义任务 (operator-authored)            [+ 新建] │
 *   │   - identical row shape, no badge                  │
 *   │   - "+ 新建任务" button lives here                 │
 *   ├────────────────────────────────────────────────────┤
 *   │ 预设任务 (preset tasks, auto-seeded)               │
 *   │   - read-only + "预设" badge inline                │
 *   │   - rows: name / channel / last status / actions   │
 *   └────────────────────────────────────────────────────┘
 *
 * The two lists come from independent ``useTasks({ kind })``
 * hooks — react-query keeps them in separate cache entries
 * (``qk.tasks({kind:"preset"})`` vs ``qk.tasks({kind:"custom"})``)
 * so a mutation on one list doesn't refetch the other.
 *
 * Polling, the runs-history drawer, and the per-row
 * "立刻跑 / 编辑 / 启用-停用 / 查看日志 / 删除" toolbar are
 * shared across both sections — the parent owns those
 * pieces of state and passes them down.
 */
import { useCallback, useEffect, useState } from "react";

import { TaskFormDrawer } from "./TaskFormDrawer";
import { RunsHistoryDrawer } from "./RunsHistoryDrawer";
import { InfoTip } from "../../components/InfoTip";
import { useT } from "../../i18n/index";
import { useQueryClient } from "@tanstack/react-query";

import {
  useDeleteTask,
  useSystemTimezone,
  useTasks,
  type TaskOut as TaskRow,
} from "../../lib/queries";
import { qk } from "../../lib/queryClient";
import { humanizeCron, humanizeRunAt } from "./cronHumanize";


// Human-readable timestamp formatter — shared between
// the row's last-status cell and the runs-history drawer.
// ISO strings from the backend are unreadable for the
// operator; we render the browser's local wall-clock plus
// a "X minutes ago" relative hint when the run is recent.
export function formatRunTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  const now = Date.now();
  const diff = now - ms;
  const abs = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ms));
  if (diff < 0) return abs;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${abs} · ${sec} 秒前`;
  if (sec < 3600) return `${abs} · ${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${abs} · ${Math.floor(sec / 3600)} 小时前`;
  if (sec < 7 * 86400) return `${abs} · ${Math.floor(sec / 86400)} 天前`;
  return abs;
}

// One row of the ``/api/tasks/{id}/runs`` response — used
// by the run-now polling loop to detect when a fire settles
// into ``success`` / ``failed``. The runner writes
// ``status="running"`` first, then transitions to a terminal
// state; the loop bails when our ``job_id`` is terminal.
export type TaskRunRow = {
  id: string;
  task_id: string;
  conversation_id: string | null;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  latency_ms: number | null;
  status: string;
  error: string | null;
  reply_excerpt: string | null;
};

export type Frequency = "hourly" | "daily" | "weekly" | "monthly" | "once";

export const WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

// Shared handlers + state every row needs — drilled down
// from the parent so both sections share the same polling
// loop / drawer state / mutation hooks without duplicating
// plumbing.
export type RowHandlers = {
  runningTasks: Map<string, string>;
  onRunNow: (t: TaskRow) => void;
  onToggle: (t: TaskRow) => void;
  onEdit: (t: TaskRow) => void;
  onDelete: (t: TaskRow) => void;
  onOpenRuns: (t: TaskRow) => void;
};

export default function TaskListPane() {
  const t = useT();
  // Two independent queries — one per section. The cache
  // entries have distinct keys so react-query refetches
  // each independently (a mutation on a custom task
  // doesn't refetch the preset list, and vice versa).
  const presetQuery = useTasks({ kind: "preset" });
  const customQuery = useTasks({ kind: "custom" });
  const qc = useQueryClient();
  const refresh = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["tasks"] });
  }, [qc]);

  const presetRows = presetQuery.data ?? null;
  const customRows = customQuery.data ?? null;
  // Surface a single error from either query — both go
  // through the same /api/tasks endpoint so showing one is
  // enough; a real backend failure lights up both queries
  // simultaneously.
  const loadError =
    (presetQuery.error ?? customQuery.error) as Error | null;

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  // ``runsForId`` is the task currently showing the runs-
  // history drawer. Clicking a row's name opens the
  // drawer for that task; clicking the close button or
  // pressing Escape clears it. The drawer's data comes
  // from ``GET /api/tasks/{id}/runs`` (already pinned by
  // the backend) and shows every fire's terminal status,
  // error summary, and reply excerpt.
  const [runsForId, setRunsForId] = useState<string | null>(null);

  const tzQuery = useSystemTimezone();
  const systemTz = tzQuery.data?.current || tzQuery.data?.default || "UTC";

  // ``runningTaskIds`` carries the task_id → job_id mapping
  // for in-flight manual fires across BOTH sections. A row
  // from either list renders the spinner while the id is
  // here. Map (not Set) so the polling loop can match the
  // exact ``job_id`` the API returned.
  const [runningTasks, setRunningTasks] = useState<
    Map<string, string>
  >(() => new Map());

  const deleteMut = useDeleteTask();

  // Polling loop for in-flight manual fires — covers
  // rows from BOTH sections because the underlying
  // ``/api/tasks/{id}/runs`` endpoint and the shared
  // ``qk.taskRuns(taskId)`` cache entry don't care about
  // source. As long as the task_id is here, the loop ticks
  // every 1.5s and evicts the id once the run settles.
  useEffect(() => {
    if (runningTasks.size === 0) return;
    let cancelled = false;
    const tick = async () => {
      for (const [taskId, runId] of runningTasks) {
        try {
          const runs = qc.getQueryData<TaskRunRow[]>(qk.taskRuns(taskId));
          if (!runs) continue;
          const mine = runs.find((r) => r.id === runId);
          if (
            mine &&
            (mine.status === "success" || mine.status === "failed")
          ) {
            setRunningTasks((prev) => {
              if (!prev.has(taskId)) return prev;
              if (prev.get(taskId) !== runId) return prev;
              const next = new Map(prev);
              next.delete(taskId);
              return next;
            });
            refresh();
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
  }, [runningTasks, qc, refresh]);

  // Foreground fetches for task runs when the polling
  // loop needs a fresh snapshot. The polling loop reads
  // from the cache via ``qc.getQueryData``; this refetch
  // fills the cache after a manual fire so the next tick
  // finds the real run row.
  useEffect(() => {
    for (const taskId of runningTasks.keys()) {
      void qc.invalidateQueries({ queryKey: qk.taskRuns(taskId) });
    }
  }, [runningTasks, qc]);

  async function deleteTask(t: TaskRow) {
    if (!confirm(`确定删除任务「${t.name}」？此操作不可撤销。`)) return;
    try {
      await deleteMut.mutateAsync(t.id);
    } catch {
      // Error surfaces on the next refetch.
    }
  }

  async function runNow(t: TaskRow) {
    setRunningTasks((prev) => {
      const next = new Map(prev);
      next.set(t.id, "__pending__");
      return next;
    });
    let job_id: string;
    try {
      const r = await fetch(`/api/tasks/${t.id}/run`, {
        method: "POST",
        credentials: "include",
      });
      if (!r.ok) throw new Error(`Run failed: ${r.status}`);
      const out = (await r.json()) as { job_id: string };
      job_id = out.job_id;
    } catch {
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
    setRunningTasks((prev) => {
      if (!prev.has(t.id)) return prev;
      const next = new Map(prev);
      next.set(t.id, job_id);
      return next;
    });
  }

  async function toggleEnabled(t: TaskRow) {
    try {
      const r = await fetch(`/api/tasks/${t.id}`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !t.enabled }),
      });
      if (!r.ok) throw new Error(`Toggle failed: ${r.status}`);
      void qc.invalidateQueries({ queryKey: ["tasks"] });
    } catch {
      // Error surfaces on the next refresh.
    }
  }

  function startEdit(t: TaskRow) {
    setEditingId(t.id);
    setDrawerOpen(true);
  }

  function startCreate() {
    setEditingId(null);
    setDrawerOpen(true);
  }

  function openRuns(t: TaskRow) {
    setRunsForId(t.id);
  }

  const rowHandlers: RowHandlers = {
    runningTasks,
    onRunNow: runNow,
    onToggle: toggleEnabled,
    onEdit: startEdit,
    onDelete: deleteTask,
    onOpenRuns: openRuns,
  };

  return (
    <div className="flex flex-col h-full min-h-0 space-y-4">
      <div className="shrink-0">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-ink">{t("sidebar.tasks")}</h2>
          <InfoTip text={t("sidebar.tasksDesc")} />
        </div>
        {systemTz && (
          <p className="mt-1 text-xs text-ink-soft">
            时区：<span className="font-mono">{systemTz}</span>，去
            <a href="/chat/scheduled-tasks?tab=settings" className="text-sky-700 ml-1">设置</a>改
          </p>
        )}
      </div>

      {loadError && <p className="form-error">✗ {loadError.message}</p>}

      {/* Custom section — operator-authored. Rendered
          FIRST (above the preset section) so the
          operator's own tasks are front-and-center; the
          "+ 新建任务" button lives here (and only here)
          because operators create their own tasks; preset
          rows are seeded automatically. */}
      <TaskSection
        title="自定义任务"
        hint="你自己创建的定时任务。"
        rows={customRows}
        loading={customQuery.isLoading}
        emptyMessage="还没有自定义任务。点 + 新建任务 创建第一条。"
        showPresetBadge={false}
        actions={
          <button
            type="button"
            onClick={startCreate}
            className="btn btn-primary text-sm py-1.5 px-3 shrink-0"
          >
            + 新建任务
          </button>
        }
        handlers={rowHandlers}
      />

      {/* Preset section — auto-seeded rows from
          ``task_presets`` templates. Rendered AFTER the
          custom section (operator-authored tasks rank
          above system-seeded ones). Read-only in the
          sense that there's no "+ 新建" button here
          (operators don't create preset tasks; they
          configure templates in Settings → 任务预设 which
          seed new assigned users). Each row is still
          individually editable / toggleable / deletable. */}
      <TaskSection
        title="预设任务"
        hint="系统按预设模板自动生成。编辑模板去 设置 → 任务预设。"
        rows={presetRows}
        loading={presetQuery.isLoading}
        emptyMessage="还没有预设任务。新增 assigned 用户时会自动 seed。"
        showPresetBadge
        handlers={rowHandlers}
      />

      {drawerOpen && (
        <TaskFormDrawer
          taskId={editingId}
          onClose={() => setDrawerOpen(false)}
          onSaved={() => {
            setDrawerOpen(false);
            refresh();
          }}
        />
      )}

      {runsForId && (() => {
        const allRows = [...(presetRows ?? []), ...(customRows ?? [])];
        const t = allRows.find((row) => row.id === runsForId);
        if (!t) return null;
        return (
          <RunsHistoryDrawer
            taskName={t.name}
            taskId={t.id}
            conversationId={t.conversation_id ?? null}
            onClose={() => setRunsForId(null)}
          />
        );
      })()}
    </div>
  );
}


// ────────────────────────────────────────────────────────────── //
// TaskSection — one of the two list panes inside TaskListPane.
// Identical row shape; the only differences are the header
// title/hint, the presence (or absence) of the "+ 新建" action
// button, and whether each row renders the inline "预设" badge.
// ────────────────────────────────────────────────────────────── //

function TaskSection({
  title,
  hint,
  rows,
  loading,
  emptyMessage,
  showPresetBadge,
  actions,
  handlers,
}: {
  title: string;
  hint: string;
  rows: TaskRow[] | null;
  loading: boolean;
  emptyMessage: string;
  showPresetBadge: boolean;
  actions?: React.ReactNode;
  handlers: RowHandlers;
}) {
  return (
    <div className="glass-card overflow-hidden flex flex-col">
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-sky-light/30">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">{title}</h3>
            {rows && rows.length > 0 && (
              <span className="text-[10px] uppercase tracking-wider text-ink-soft font-mono rounded bg-sky-pale/40 px-1.5 py-0.5">
                {rows.length}
              </span>
            )}
          </div>
          <p className="text-[11px] text-ink-soft mt-0.5">{hint}</p>
        </div>
        {actions}
      </div>
      {loading && rows === null ? (
        <p className="p-6 text-sm text-ink-soft">加载中…</p>
      ) : rows && rows.length === 0 ? (
        <p className="p-6 text-sm text-ink-soft">{emptyMessage}</p>
      ) : rows && rows.length > 0 ? (
        <div className="max-h-[40vh] overflow-y-auto">
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
                <TaskRowView
                  key={t.id}
                  task={t}
                  showPresetBadge={showPresetBadge}
                  handlers={handlers}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}


function TaskRowView({
  task: t,
  showPresetBadge,
  handlers,
}: {
  task: TaskRow;
  showPresetBadge: boolean;
  handlers: RowHandlers;
}) {
  const isRunning = handlers.runningTasks.has(t.id);
  return (
    <tr
      className={
        "border-b border-sky-light/30 last:border-0 " +
        (t.enabled ? "" : "opacity-60")
      }
    >
      <td className="py-2 pr-4 text-ink font-medium">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => handlers.onOpenRuns(t)}
            title="点击查看运行历史"
            className="text-left font-medium text-ink hover:text-sky-deep underline-offset-2 hover:underline cursor-pointer"
          >
            {t.name}
          </button>
          {/* Inline preset badge — only in the preset
              section. Identifies the source template at
              a glance without requiring the operator to
              cross-reference the Settings card. */}
          {showPresetBadge && t.preset_key && (
            <span className="inline-block rounded bg-sky-pale/40 text-ink-soft text-[10px] font-mono px-1.5 py-px">
              预设 · {t.preset_key}
            </span>
          )}
          <button
            type="button"
            onClick={() => handlers.onEdit(t)}
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
              <path d="M11 3 L13 5 L5.5 12.5 L3 13 L3.5 10.5 Z" />
              <path d="M11 3 L13 5 L14 4 L12 2 Z" />
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
        {t.run_at ? (
          <span title={t.run_at}>
            一次性 · {humanizeRunAt(t.run_at)}
          </span>
        ) : (
          <span title={t.cron}>{humanizeCron(t.cron)}</span>
        )}
      </td>
      <td className="py-2 pr-4 text-ink-soft text-xs">
        <div
          title={
            t.delivery_to === null
              ? "未指定 — operator 绑定"
              : t.delivery_to
          }
        >
          {t.target_channel === "tg"
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
        {isRunning ? (
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
          <button
            type="button"
            onClick={() => handlers.onRunNow(t)}
            disabled={!t.enabled}
            title="立刻跑"
            aria-label="立刻跑"
            className="w-7 h-7 inline-flex items-center justify-center rounded-md text-sky-700 hover:text-sky-deep hover:bg-sky-pale/40 transition disabled:text-sky-light/50 disabled:cursor-not-allowed"
          >
            ▶
          </button>
          <button
            type="button"
            onClick={() => handlers.onToggle(t)}
            title={t.enabled ? "停用" : "启用"}
            aria-label={t.enabled ? "停用" : "启用"}
            className="w-7 h-7 inline-flex items-center justify-center rounded-md text-sky-700 hover:text-sky-deep hover:bg-sky-pale/40 transition"
          >
            {t.enabled ? "⏸" : "▶▶"}
          </button>
          <button
            type="button"
            onClick={() => handlers.onOpenRuns(t)}
            title="查看日志"
            aria-label="查看日志"
            className="w-7 h-7 inline-flex items-center justify-center rounded-md text-sky-700 hover:text-sky-deep hover:bg-sky-pale/40 transition"
          >
            💬
          </button>
          <button
            type="button"
            onClick={() => handlers.onDelete(t)}
            title="删除"
            aria-label="删除"
            className="w-7 h-7 inline-flex items-center justify-center rounded-md text-rose-700 hover:text-rose-900 hover:bg-rose-50 transition"
          >
            🗑
          </button>
        </div>
      </td>
    </tr>
  );
}