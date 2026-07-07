/**
 * TaskListPane — operator-facing CRUD over scheduled tasks.
 *
 * Lives on the Chat sidebar → "定时任务" entry
 * (``id="scheduled-tasks"``). Talks to the
 * ``/api/tasks`` endpoints; defers human-language
 * cron rendering to the backend's ``humanize_cron``-style
 * helper if it's added later (v0 just shows the raw cron).
 *
 * Layout:
 *   - Header row: title + "+ 新建任务" button → opens form drawer.
 *   - Filter chips: all / enabled / disabled.
 *   - Table: name / cron / tz / channel / last status /
 *     last_run_at / actions.
 *
 * Reactions:
 *   - Drawer: name, prompt, cron, tz, channel, employee_id
 *     (rendered as a select populated from
 *     ``GET /api/employees?page=1&page_size=100``).
 *   - Each row's actions: 「立刻跑」 / 启用/停用 toggle /
 *     「删除」.
 *   - After any mutation, refresh the list. The state
 *     is local — no global store because the volume
 *     (a few dozen rows in any sane deploy) doesn't
 *     warrant one.
 */
import { useEffect, useState } from "react";

type TaskRow = {
  id: string;
  name: string;
  prompt: string;
  cron: string;
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

type EmployeeMini = { id: number; name: string; display_name: string | null };

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

export default function TaskListPane() {
  const [rows, setRows] = useState<TaskRow[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [employees, setEmployees] = useState<EmployeeMini[]>([]);

  // Initial load + after every refresh trigger.
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

  // One-shot employees fetch for the drawer select.
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/employees?page=1&page_size=100", {
          credentials: "include",
        });
        if (r.ok) {
          const body = (await r.json()) as { items: EmployeeMini[] };
          setEmployees(body.items ?? []);
        }
      } catch {
        /* drawer will just show empty select */
      }
    })();
  }, []);

  // Refresh when the filter chips change.
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
            按 cron 时间到点跑任务，每次会话独立 — operator 在 chat 历史能看到每一次的回复。
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
                <th className="py-2 pr-4 font-medium">Cron</th>
                <th className="py-2 pr-4 font-medium">时区</th>
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
                  <td className="py-2 pr-4 text-ink-soft font-mono text-xs">
                    {t.cron}
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs">
                    {t.tz}
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
          employees={employees}
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
// Drawer — single inline form, used for both create and edit. Fields are
// pre-populated when ``taskId`` is provided; otherwise blank.
// ──────────────────────────────────────────────────────────────────────── #

function TaskFormDrawer(props: {
  taskId: string | null;
  employees: EmployeeMini[];
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}) {
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [cron, setCron] = useState("");
  const [tz, setTz] = useState("UTC");
  const [channel, setChannel] = useState<"webui" | "tg">("webui");
  const [employeeId, setEmployeeId] = useState<string>("");
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<string | null>(null);

  // Pre-populate when editing.
  useEffect(() => {
    if (props.taskId === null) {
      setLoaded(null);
      setName("");
      setPrompt("");
      setCron("");
      setTz("UTC");
      setChannel("webui");
      setEnabled(true);
      setError(null);
      // pre-select current admin employee if available.
      if (props.employees.length === 1) {
        setEmployeeId(String(props.employees[0].id));
      }
      return;
    }
    if (loaded === props.taskId) return;
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
        setCron(t.cron);
        setTz(t.tz);
        setChannel(t.channel);
        setEmployeeId(String(t.employee_id));
        setEnabled(t.enabled);
        setLoaded(t.id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Network error");
      }
    })();
  }, [props.taskId, props.employees, loaded]);

  async function save() {
    setError(null);
    if (!name.trim() || !prompt.trim() || !cron.trim() || !employeeId) {
      setError("名称 / prompt / cron / 员工 不能为空");
      return;
    }
    const payload = {
      name: name.trim(),
      prompt: prompt.trim(),
      cron: cron.trim(),
      tz: tz.trim() || "UTC",
      channel,
      employee_id: Number(employeeId),
      enabled,
    };
    setSaving(true);
    try {
      const path = props.taskId ? `/${props.taskId}` : "";
      const init: RequestInit = {
        method: props.taskId ? "PATCH" : "POST",
        body: JSON.stringify(props.taskId ? { ...payload } : payload),
      };
      // PATCH lets us omit unchanged fields; the API
      // wraps each in model_dump(exclude_unset=True).
      const r = await fetch(`/api/tasks${path}`, {
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        ...init,
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setError(
          (body as { detail?: string }).detail ??
            `${r.status} ${r.statusText}`,
        );
        return;
      }
      await props.onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
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
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="task-cron" className="form-label">Cron 表达式</label>
              <input
                id="task-cron"
                type="text"
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                placeholder="0 9 * * *  (每天 09:00)"
                className="form-input text-sm py-2 px-3 font-mono"
              />
              <p className="mt-1 text-[10px] text-ink-soft">
                5 字段：分 时 日 月 周。例：<span className="font-mono">*/5 * * * *</span> 每 5 分钟 · <span className="font-mono">0 9 * * mon-fri</span> 工作日 09:00
              </p>
            </div>
            <div>
              <label htmlFor="task-tz" className="form-label">时区 (IANA)</label>
              <input
                id="task-tz"
                type="text"
                value={tz}
                onChange={(e) => setTz(e.target.value)}
                placeholder="UTC"
                className="form-input text-sm py-2 px-3 font-mono"
              />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="task-channel" className="form-label">Channel</label>
              <select
                id="task-channel"
                value={channel}
                onChange={(e) =>
                  setChannel(e.target.value as "webui" | "tg")
                }
                className="form-input text-sm py-2 px-3"
              >
                <option value="webui">webui（写到 chat 历史）</option>
                <option value="tg">tg（同时推到 TG）</option>
              </select>
            </div>
            <div>
              <label htmlFor="task-employee" className="form-label">凭据（员工）</label>
              <select
                id="task-employee"
                value={employeeId}
                onChange={(e) => setEmployeeId(e.target.value)}
                className="form-input text-sm py-2 px-3"
              >
                <option value="">— 选一个 —</option>
                {props.employees.map((e) => (
                  <option key={e.id} value={String(e.id)}>
                    {e.display_name || e.name} (#{e.id})
                  </option>
                ))}
              </select>
            </div>
          </div>
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
