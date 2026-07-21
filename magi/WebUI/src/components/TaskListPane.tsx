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
 * or assigned employee — the backend's role gate
 * refuses other roles); the timezone is read from the
 * Settings panel's ``system.timezone`` field globally.
 */
import { useEffect, useState } from "react";

import { humanizeCron, humanizeRunAt } from "./cronHumanize";

type TaskRow = {
  id: string;
  name: string;
  prompt: string;
  cron: string;
  // ``run_at`` carries the ISO timestamp for ``frequency="once"``
  // tasks. Mutually exclusive with ``cron`` in the row — see
  // the cell render below.
  run_at: string | null;
  // ``delivery_to`` is the concrete destination: TG chat_id,
  // ``"new"`` for fresh-session webui fires, an explicit
  // chat session_id, or null (operator-bound fallback at
  // fire time). The cell renders a "→ <target>" snippet
  // below the schedule row so the operator can audit the
  // delivery site at a glance.
  delivery_to: string | null;
  tz: string;
  channel: "webui" | "tg";
  employee_id: number;
  enabled: boolean;
  consecutive_failures: number;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

// One row of the ``/api/tasks/{id}/runs`` response — used
// by the run-now polling loop to detect when a fire settles
// into ``success`` / ``failed``. The runner writes
// ``status="running"`` first, then transitions to a terminal
// state; the loop bails when our ``run_id`` is terminal.
type TaskRunRow = {
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

type Frequency = "hourly" | "daily" | "weekly" | "monthly" | "once";
type Filter = "all" | "enabled" | "disabled";

async function api<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const r = await fetch(`/api/tasks${path}`, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${body.slice(0, 200)}`);
  }
  if (r.status === 204) return null as T;
  return (await r.json()) as T;
}

const WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export default function TaskListPane() {
  const [rows, setRows] = useState<TaskRow[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [loadError, setLoadError] = useState<string | null>(null);
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

  async function refresh() {
    setLoadError(null);
    try {
      const params = new URLSearchParams();
      if (filter !== "all") {
        params.set("enabled", filter === "enabled" ? "true" : "false");
      }
      const qs = params.toString();
      const data = await api<TaskRow[]>(`${qs ? "?" + qs : ""}`);
      setRows(data ?? []);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
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

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

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

  async function deleteTask(t: TaskRow) {
    if (!confirm(`确定删除任务「${t.name}」？此操作不可撤销。`)) return;
    try {
      await api<void>(`/${t.id}`, { method: "DELETE" });
      await refresh();
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "delete failed");
    }
  }

  async function runNow(t: TaskRow) {
    let run_id: string;
    try {
      const out = await api<{ run_id: string }>(
        `/${t.id}/run`, { method: "POST" },
      );
      run_id = out.run_id;
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "run now failed");
      return;
    }
    // Optimistic local state: mark this row as in-flight
    // before the runner's first DB write, so the status
    // cell flips to the spinner on the same frame as the
    // click. The polling effect (below) evicts the entry
    // when the runner writes a terminal status.
    setRunningTasks((prev) => {
      const next = new Map(prev);
      next.set(t.id, run_id);
      return next;
    });
    await refresh();
  }

  async function toggleEnabled(t: TaskRow) {
    try {
      await api<TaskRow>(`/${t.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !t.enabled }),
      });
      await refresh();
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "toggle failed");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
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

      <div className="glass-card overflow-hidden">
        {rows === null && !loadError ? (
          <p className="p-6 text-sm text-ink-soft">加载中…</p>
        ) : rows && rows.length === 0 ? (
          <p className="p-6 text-sm text-ink-soft">还没有定时任务。点 + 新建任务 创建第一条。</p>
        ) : rows && rows.length > 0 ? (
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
                        <span className="ml-1 text-[10px] text-ink-soft/70 font-normal">
                          ↗ 日志
                        </span>
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
                      TG chat_id are rendered verbatim.
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
                    <div className="flex items-center justify-end gap-2 text-xs">
                      <button
                        type="button"
                        onClick={() => runNow(t)}
                        disabled={!t.enabled}
                        className="text-sky-700 hover:text-sky-deep transition disabled:text-sky-light/50 disabled:cursor-not-allowed"
                      >
                        立刻跑
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleEnabled(t)}
                        className="text-sky-700 hover:text-sky-deep transition"
                      >
                        {t.enabled ? "停用" : "启用"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setEditingId(t.id);
                          setDrawerOpen(true);
                        }}
                        className="text-sky-700 hover:text-sky-deep transition"
                      >
                        编辑
                      </button>
                      {/* Runs-history entry. The operator
                          can also click the row's task
                          name, but a dedicated button is
                          more discoverable — it makes the
                          affordance obvious without
                          requiring a hover experiment.
                          Opens the same drawer as the
                          terminal-tick auto-pop, just
                          without the spinner-then-flip
                          context. */}
                      <button
                        type="button"
                        onClick={() => setRunsForId(t.id)}
                        className="text-sky-700 hover:text-sky-deep transition"
                      >
                        查看日志
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteTask(t)}
                        className="text-rose-700 hover:text-rose-900 transition"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>

      {drawerOpen && (
        <TaskFormDrawer
          taskId={editingId}
          onClose={() => setDrawerOpen(false)}
          onSaved={async () => {
            setDrawerOpen(false);
            await refresh();
          }}
        />
      )}

      {runsForId && (
        <RunsHistoryDrawer
          taskId={runsForId}
          onClose={() => setRunsForId(null)}
        />
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────── #
// Drawer
// ──────────────────────────────────────────────────────────────────────── #

function TaskFormDrawer(props: {
  taskId: string | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}) {
  // Form state. Editing loads the row; we don't try to
  // round-trip the preset back from cron (back-conversion
  // is ambiguous — ``0 9 * * 1`` could be Weekly Mon@09:00
  // OR Monthly DOM=1@09:00). For edit, we re-load with
  // the saved preset fields if they roundtrip cleanly,
  // else we leave the operator to re-pick.
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [frequency, setFrequency] = useState<Frequency>("daily");
  const [hour, setHour] = useState(0);
  const [minute, setMinute] = useState(0);
  const [dayOfWeek, setDayOfWeek] = useState(0); // Mon = 0
  const [dayOfMonth, setDayOfMonth] = useState(1);
  // `once`-shape: ISO datetime-local string ("YYYY-MM-DDTHH:MM")
  // — the Web form's canonical picker format, accepted by
  // ``<input type="datetime-local">``. The client converts
  // to a full ISO datetime (with local-tz offset, no Z) on
  // submit; the server's ``validate_run_at`` parser is
  // lenient about Z-marker presence.
  const [runAt, setRunAt] = useState("");
  const [channel, setChannel] = useState<"webui" | "tg">("webui");
  // ``delivery_to`` is server-derived per the unified rule:
  //   channel=webui → "new" (every fire spawns a fresh session)
  //   channel=tg    → operator.telegram_id (server-side; the
  //                   form doesn't pick — and 400s if not bound)
  // The form no longer asks. The table's "→ <target>" snippet
  // is rendered from the row's resolved value.
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (props.taskId === null) {
      setName("");
      setPrompt("");
      setFrequency("daily");
      setHour(0);
      setMinute(0);
      setDayOfWeek(0);
      setDayOfMonth(1);
      setRunAt("");
      setChannel("webui");
      setEnabled(true);
      setError(null);
      return;
    }
    (async () => {
      try {
        const r = await fetch(`/api/tasks/${props.taskId}`, {
          credentials: "include",
        });
        if (!r.ok) {
          setError(`加载失败 (${r.status})`);
          return;
        }
        const t = (await r.json()) as TaskRow;
        setName(t.name);
        setPrompt(t.prompt);
        setChannel(t.channel);
        // ``delivery_to`` is server-derived; the drawer
        // doesn't pre-fill or surface it. The cell snippet
        // (rendered elsewhere in this component) shows the
        // resolved value from the row.
        setEnabled(t.enabled);
        // If the row is a once-shot, pre-fill the form
        // with ``once`` + the ISO trimmed to ``YYYY-MM-DDTHH:MM``
        // (the format ``<input type="datetime-local">`` expects).
        // ``datetime-local`` has no timezone picker; we
        // leave the form in operator-local mode and let
        // ``toISOFromLocal`` carry the offset at submit.
        if (t.run_at) {
          setFrequency("once");
          const m = t.run_at.match(
            /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/,
          );
          setRunAt(m ? `${m[1]}T${m[2]}` : "");
        } else {
          // Preset-into-cron is ambiguous, so we don't
          // try to derive the preset from cron. The schedule
          // cell renders a humanised phrase off the row's
          // ``run_at`` / ``cron`` shape; the edit form
          // starts from safe defaults and the operator picks
          // the preset on save.
          setFrequency("daily");
          setHour(0);
          setMinute(0);
          setDayOfWeek(0);
          setDayOfMonth(1);
          setRunAt("");
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Network error");
      }
    })();
  }, [props.taskId]);

  async function save() {
    setError(null);
    if (!name.trim() || !prompt.trim()) {
      setError("名称 和 prompt 不能为空");
      return;
    }
    if (frequency === "weekly" && (dayOfWeek < 0 || dayOfWeek > 6)) {
      setError("请选择星期");
      return;
    }
    if (frequency === "monthly" && (dayOfMonth < 1 || dayOfMonth > 31)) {
      setError("请选择 1-31");
      return;
    }
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) {
      setError("小时必须 0-23");
      return;
    }
    if (!Number.isInteger(minute) || minute < 0 || minute > 59) {
      setError("分钟必须 0-59");
      return;
    }
    if (frequency === "once" && !runAt) {
      setError("请选择触发时间");
      return;
    }

    const body: Record<string, unknown> = {
      name: name.trim(),
      prompt: prompt.trim(),
      frequency,
      hour,
      minute,
      channel,
      enabled,
    };
    if (frequency === "weekly") body["day_of_week"] = dayOfWeek;
    if (frequency === "monthly") body["day_of_month"] = dayOfMonth;
    // ``delivery_to`` is server-derived from channel +
    // operator.telegram_id; the form does not send it.
    // ``<input type="datetime-local">`` returns a
    // timezone-less string. The operator's browser TZ is
    // usually the same as their admin machine's clock;
    // we send the local-time + offset (the negative of
    // ``Date.getTimezoneOffset()``) so a Shanghai operator
    // sees the cron fire at the wall-clock they picked.
    if (frequency === "once" && runAt) {
      const d = new Date(runAt);
      const offset = -d.getTimezoneOffset();
      const sign = offset >= 0 ? "+" : "-";
      const oh = String(Math.floor(Math.abs(offset) / 60)).padStart(2, "0");
      const om = String(Math.abs(offset) % 60).padStart(2, "0");
      body["run_at"] = `${runAt}:00${sign}${oh}:${om}`;
    }

    setSaving(true);
    try {
      const path = props.taskId ? `/${props.taskId}` : "";
      const r = await fetch(`/api/tasks${path}`, {
        method: props.taskId ? "PATCH" : "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        setError(detail.detail ?? `${r.status} ${r.statusText}`);
        return;
      }
      await props.onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  // HH:MM string helpers (only for daily / weekly / monthly).
  function setHHMM(h: number, m: number) {
    setHour(h);
    setMinute(m);
  }

  return (
    <div className="fixed inset-0 z-40 bg-black/20 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-sky-light/40 flex items-center justify-between">
          <h3 className="text-base font-semibold text-ink">
            {props.taskId ? "编辑任务" : "新建任务"}
          </h3>
          <button
            type="button"
            onClick={props.onClose}
            className="text-ink-soft hover:text-ink text-sm"
          >
            ✕ 关闭
          </button>
        </div>
        <div className="p-6 space-y-4">
          <div>
            <label htmlFor="task-name" className="form-label">名称</label>
            <input
              id="task-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：每天早上查 S&P 500 收盘"
              maxLength={120}
              className="form-input text-sm py-2 px-3"
            />
          </div>
          <div>
            <label htmlFor="task-prompt" className="form-label">
              Prompt（自然语言 — 每次到点会作为新会话的 user message 跑）
            </label>
            <textarea
              id="task-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={5}
              placeholder="例如：查 S&P 500 当日收盘价，列出 top 5 movers，简要分析每个为何变动"
              className="form-input text-sm py-2 px-3 font-mono resize-y"
            />
          </div>

          {/* Preset + moment row — the v2 contract. Four
              controls alongside (the user layout shows
              them in a row, like the screenshot). */}
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
            <div>
              <label htmlFor="task-frequency" className="form-label">触发方式</label>
              <select
                id="task-frequency"
                value={frequency}
                onChange={(e) => setFrequency(e.target.value as Frequency)}
                className="form-input text-sm py-2 px-3"
              >
                <option value="hourly">每小时</option>
                <option value="daily">每日</option>
                <option value="weekly">每周</option>
                <option value="monthly">每月</option>
                <option value="once">一次性</option>
              </select>
            </div>

            {/* Once — single ISO datetime picker. Moment
                fields above (hour / weekday / dom) are
                ignored on this branch; only ``runAt`` is
                read. ``datetime-local`` gives us the
                browser-local wall-clock; we attach the
                operator's TZ offset at submit so a Shanghai
                admin picks 15:30 and that 15:30 Shanghai is
                what the task fires at, not 15:30 UTC. */}
            {frequency === "once" && (
              <div className="sm:col-span-2">
                <label htmlFor="task-run-at" className="form-label">
                  触发时间（本地时区）
                </label>
                <input
                  id="task-run-at"
                  type="datetime-local"
                  value={runAt}
                  onChange={(e) => setRunAt(e.target.value)}
                  className="form-input text-sm py-2 px-3"
                />
              </div>
            )}

            {/* Hourly — minute (0..59) only. */}
            {frequency === "hourly" && (
              <div>
                <label htmlFor="task-minute" className="form-label">分钟 (0-59)</label>
                <select
                  id="task-minute"
                  value={minute}
                  onChange={(e) => setMinute(Number(e.target.value))}
                  className="form-input text-sm py-2 px-3"
                >
                  {Array.from({ length: 60 }, (_, m) => (
                    <option key={m} value={m}>
                      :{m.toString().padStart(2, "0")}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Daily — HH:MM. */}
            {frequency === "daily" && (
              <>
                <div>
                  <label htmlFor="task-hour" className="form-label">小时</label>
                  <select
                    id="task-hour"
                    value={hour}
                    onChange={(e) => setHHMM(Number(e.target.value), minute)}
                    className="form-input text-sm py-2 px-3"
                  >
                    {Array.from({ length: 24 }, (_, h) => (
                      <option key={h} value={h}>
                        {h.toString().padStart(2, "0")}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="task-minute" className="form-label">分钟</label>
                  <select
                    id="task-minute"
                    value={minute}
                    onChange={(e) => setHHMM(hour, Number(e.target.value))}
                    className="form-input text-sm py-2 px-3"
                  >
                    {Array.from({ length: 60 }, (_, m) => (
                      <option key={m} value={m}>
                        {m.toString().padStart(2, "0")}
                      </option>
                    ))}
                  </select>
                </div>
              </>
            )}

            {/* Weekly — weekday + HH:MM. */}
            {frequency === "weekly" && (
              <>
                <div>
                  <label htmlFor="task-weekday" className="form-label">星期</label>
                  <select
                    id="task-weekday"
                    value={dayOfWeek}
                    onChange={(e) => setDayOfWeek(Number(e.target.value))}
                    className="form-input text-sm py-2 px-3"
                  >
                    {WEEKDAY_LABELS.map((label, i) => (
                      <option key={i} value={i}>{label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="task-hour" className="form-label">小时</label>
                  <select
                    id="task-hour"
                    value={hour}
                    onChange={(e) => setHHMM(Number(e.target.value), minute)}
                    className="form-input text-sm py-2 px-3"
                  >
                    {Array.from({ length: 24 }, (_, h) => (
                      <option key={h} value={h}>
                        {h.toString().padStart(2, "0")}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="task-minute" className="form-label">分钟</label>
                  <select
                    id="task-minute"
                    value={minute}
                    onChange={(e) => setHHMM(hour, Number(e.target.value))}
                    className="form-input text-sm py-2 px-3"
                  >
                    {Array.from({ length: 60 }, (_, m) => (
                      <option key={m} value={m}>
                        {m.toString().padStart(2, "0")}
                      </option>
                    ))}
                  </select>
                </div>
              </>
            )}

            {/* Monthly — DOM + HH:MM. */}
            {frequency === "monthly" && (
              <>
                <div>
                  <label htmlFor="task-dom" className="form-label">几日</label>
                  <select
                    id="task-dom"
                    value={dayOfMonth}
                    onChange={(e) => setDayOfMonth(Number(e.target.value))}
                    className="form-input text-sm py-2 px-3"
                  >
                    {Array.from({ length: 31 }, (_, d) => (
                      <option key={d + 1} value={d + 1}>
                        {d + 1}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="task-hour" className="form-label">小时</label>
                  <select
                    id="task-hour"
                    value={hour}
                    onChange={(e) => setHHMM(Number(e.target.value), minute)}
                    className="form-input text-sm py-2 px-3"
                  >
                    {Array.from({ length: 24 }, (_, h) => (
                      <option key={h} value={h}>
                        {h.toString().padStart(2, "0")}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="task-minute" className="form-label">分钟</label>
                  <select
                    id="task-minute"
                    value={minute}
                    onChange={(e) => setHHMM(hour, Number(e.target.value))}
                    className="form-input text-sm py-2 px-3"
                  >
                    {Array.from({ length: 60 }, (_, m) => (
                      <option key={m} value={m}>
                        {m.toString().padStart(2, "0")}
                      </option>
                    ))}
                  </select>
                </div>
              </>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="task-channel" className="form-label">Channel</label>
              <select
                id="task-channel"
                value={channel}
                onChange={(e) => setChannel(e.target.value as "webui" | "tg")}
                className="form-input text-sm py-2 px-3"
              >
                <option value="webui">webui（写到 chat 历史）</option>
                <option value="tg">tg（同时推到 TG）</option>
              </select>
              {/* ``delivery_to`` is no longer a form control:
                  server-derived from channel + operator.
                  channel=webui → "new"; channel=tg → operator's
                  bound telegram_id (400 if unbound). The cell
                  snippet further down renders the resolved value. */}
            </div>
            <div className="text-xs text-ink-soft self-end pb-2">
              投递目标自动决定：webui 每次新建会话，tg 推到 operator 绑定的 TG chat
            </div>
          </div>

          <p className="text-xs text-ink-soft">
            时区和凭据由系统自动决定：cron 用 Settings → 系统时区；凭据用当前登录者（admin 或「被此 MAGI 服务」的 assigned）的 provider / API key。
          </p>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="accent-sky-deep"
            />
            启用（取消勾选 = 停止调度）
          </label>
          {error && <p className="form-error">✗ {error}</p>}
        </div>
        <div className="px-6 py-4 border-t border-sky-light/40 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={props.onClose}
            className="btn btn-secondary text-sm py-2 px-4"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="btn btn-primary text-sm py-2 px-4"
          >
            {saving ? "保存中…" : props.taskId ? "保存改动" : "创建任务"}
          </button>
        </div>
      </div>
    </div>
  );
}


// ──────────────────────────────────────────────────────────────────────── #
// Runs history drawer
// ──────────────────────────────────────────────────────────────────────── #

function RunsHistoryDrawer(props: {
  taskId: string;
  onClose: () => void;
}) {
  // Pulls ``GET /api/tasks/{id}/runs`` once on open and
  // shows the last 20 fires newest-first. Each row
  // surfaces:
  //
  //   - status (success / failed / running) — colour-coded
  //   - trigger (manual / cron) — the agent runs the same
  //     loop either way, but the operator usually wants
  //     to know which path they tripped
  //   - finished_at + latency_ms — the bill for the fire
  //   - reply_excerpt — the agent's last assistant turn
  //     truncated to 500 chars. This is where the
  //     "did the TG push actually fire?" question is
  //     answered: if ``channel=tg`` and the agent's
  //     reply is here but the operator's TG is silent,
  //     the bot wasn't registered at fire-time.
  //   - error — for ``failed`` rows, the truncated
  //     error summary (last_error on the task gets the
  //     same string; the per-run copy here survives
  //     later successful fires).
  //
  // No edit / re-run affordance — the row in the main
  // table already has those. v0 ships read-only history.
  const [runs, setRuns] = useState<TaskRunRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(
          `/api/tasks/${props.taskId}/runs`,
          { credentials: "include" },
        );
        if (!r.ok) {
          setLoadError(`加载失败 (${r.status})`);
          return;
        }
        const data = (await r.json()) as TaskRunRow[];
        if (!cancelled) setRuns(data);
      } catch (err) {
        if (!cancelled) {
          setLoadError(
            err instanceof Error ? err.message : "Network error",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [props.taskId]);

  return (
    <div className="fixed inset-0 z-40 bg-black/20 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl max-w-3xl w-full max-h-[90vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-sky-light/40 flex items-center justify-between">
          <h3 className="text-base font-semibold text-ink">
            运行历史 · {props.taskId.slice(0, 8)}…
          </h3>
          <button
            type="button"
            onClick={props.onClose}
            className="text-ink-soft hover:text-ink text-sm"
          >
            ✕ 关闭
          </button>
        </div>
        <div className="p-6 space-y-3">
          {loadError && <p className="form-error">✗ {loadError}</p>}
          {runs === null && !loadError ? (
            <p className="text-sm text-ink-soft">加载中…</p>
          ) : runs && runs.length === 0 ? (
            <p className="text-sm text-ink-soft">
              这条任务还没有任何 fire 记录。点表格里的「立刻跑」或等下一次 cron 触发。
            </p>
          ) : runs ? (
            runs.map((r) => (
              <div
                key={r.id}
                className="border border-sky-light/30 rounded-lg p-3 text-xs space-y-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={
                        r.status === "success"
                          ? "text-emerald-700 font-medium"
                          : r.status === "failed"
                            ? "text-rose-700 font-medium"
                            : "text-sky-700 font-medium"
                      }
                    >
                      {r.status === "success"
                        ? "✓ 成功"
                        : r.status === "failed"
                          ? "✗ 失败"
                          : r.status === "running"
                            ? "⟳ 执行中"
                            : r.status}
                    </span>
                    <span className="text-ink-soft">
                      · {r.trigger === "manual" ? "手动" : "定时"}
                    </span>
                    {r.latency_ms != null && (
                      <span className="text-ink-soft">
                        · {r.latency_ms} ms
                      </span>
                    )}
                  </div>
                  <span className="text-ink-soft/80 font-mono text-[10px]">
                    {r.finished_at ?? r.started_at}
                  </span>
                </div>
                {r.reply_excerpt && (
                  <div className="bg-sky-light/10 rounded p-2 font-mono text-[11px] whitespace-pre-wrap break-words">
                    {r.reply_excerpt}
                  </div>
                )}
                {r.error && (
                  <div className="bg-rose-50 text-rose-900 rounded p-2 font-mono text-[11px] whitespace-pre-wrap break-words">
                    {r.error}
                  </div>
                )}
              </div>
            ))
          ) : null}
        </div>
      </div>
    </div>
  );
}
