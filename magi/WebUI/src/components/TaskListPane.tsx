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
  const [systemTz, setSystemTz] = useState<string | null>(null);

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
    try {
      await api<{ run_id: string }>(`/${t.id}/run`, { method: "POST" });
      await refresh();
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "run now failed");
    }
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
                <th className="py-2 pr-4 font-medium">Cron（自动生成）</th>
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
                      <span>{t.name}</span>
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
                    */}
                    {t.run_at ? (
                      <span title={t.run_at}>
                        一次性 · {humanizeRunAt(t.run_at)}
                      </span>
                    ) : (
                      <span title={t.cron}>{humanizeCron(t.cron)}</span>
                    )}
                    {/*
                      Delivery destination — a separate
                      line under the schedule so the cell
                      reads as "every day 09:00 → 新会话".
                      ``"new"`` is the magic token; explicit
                      session_id / TG chat_id are rendered
                      verbatim (the cell's tooltip already
                      shows the raw cron, so duplicating the
                      verbatim string here is intentional).
                    */}
                    <div
                      className="mt-0.5 text-[10px] text-ink-soft/80 font-mono"
                      title={
                        t.delivery_to === null
                          ? "未指定 — operator 绑定"
                          : t.delivery_to
                      }
                    >
                      →{" "}
                      {t.delivery_to === null
                        ? "未指定"
                        : t.delivery_to === "new"
                          ? "新会话"
                          : t.delivery_to}
                    </div>
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs">
                    {t.channel}
                  </td>
                  <td className="py-2 pr-4 text-xs">
                    {t.last_status ? (
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
