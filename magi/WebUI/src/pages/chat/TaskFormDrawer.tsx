/**
 * TaskFormDrawer — create / edit form for a scheduled task.
 *
 * Owns the form state and the preset (frequency + moment) row
 * of the v2 contract. On submit it POSTs or PATCHes
 * ``/api/tasks`` and calls ``onSaved`` so the parent can
 * refresh its list.
 *
 * Migrated to react-query: ``useTask`` populates the form
 * for edit; ``useCreateTask`` / ``useUpdateTask`` cover the
 * save paths. The form state itself stays in ``useState`` —
 * react-query only owns the network round-trip. The
 * form-fill effect is guarded by ``useRef`` so a
 * ``refetchOnWindowFocus`` doesn't blow away the operator's
 * in-flight edits.
 */
import { useEffect, useRef, useState } from "react";

import { useCreateTask, useTask, useUpdateTask } from "../../lib/queries";
import { WEEKDAY_LABELS } from "./TaskListPane";
import type { Frequency } from "./TaskListPane";
import Toggle from "../../components/Toggle";

export function TaskFormDrawer(props: {
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
  const [target_channel, setTargetChannel] = useState<"webui" | "tg">("webui");
  // ``delivery_to`` is server-derived per the unified rule:
  //   channel=webui → "new" (every fire spawns a fresh session)
  //   channel=tg    → operator.tgid (server-side; the
  //                   form doesn't pick — and 400s if not bound)
  // The form no longer asks. The table's "→ <target>" snippet
  // is rendered from the row's resolved value.
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Edit-mode row fetch. ``enabled: taskId !== null`` so
  // the create-mode drawer doesn't fire a needless
  // ``GET /api/tasks/null``.
  const taskQuery = useTask(props.taskId);
  const createMut = useCreateTask();
  const updateMut = useUpdateTask();

  // Hydrate the form once per taskId. The ref guard skips
  // re-hydration on subsequent refetches (e.g.
  // refetchOnWindowFocus) so the operator's later edits
  // aren't blown away.
  const lastHydratedId = useRef<string | null>(null);
  useEffect(() => {
    if (!taskQuery.data) return;
    if (lastHydratedId.current === props.taskId) return;
    lastHydratedId.current = props.taskId;
    const t = taskQuery.data;
    setName(t.name);
    setPrompt(t.prompt);
    setTargetChannel(t.target_channel);
    setEnabled(t.enabled);
    if (t.run_at) {
      setFrequency("once");
      const m = t.run_at.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
      setRunAt(m ? `${m[1]}T${m[2]}` : "");
    } else {
      setFrequency("daily");
      setHour(0);
      setMinute(0);
      setDayOfWeek(0);
      setDayOfMonth(1);
      setRunAt("");
    }
  }, [taskQuery.data, props.taskId]);

  // Reset the form (and the hydration ref) when the
  // drawer opens for create.
  useEffect(() => {
    if (props.taskId !== null) return;
    lastHydratedId.current = null;
    setName("");
    setPrompt("");
    setFrequency("daily");
    setHour(0);
    setMinute(0);
    setDayOfWeek(0);
    setDayOfMonth(1);
    setRunAt("");
    setTargetChannel("webui");
    setEnabled(true);
  }, [props.taskId]);

  // Surface query errors as the form-level error.
  useEffect(() => {
    if (!taskQuery.error) return;
    setError(`加载失败 (${(taskQuery.error as Error).message})`);
  }, [taskQuery.error]);

  function toBody(): Record<string, unknown> {
    const body: Record<string, unknown> = {
      name: name.trim(),
      prompt: prompt.trim(),
      frequency,
      hour,
      minute,
      target_channel,
      enabled,
    };
    if (frequency === "weekly") body["day_of_week"] = dayOfWeek;
    if (frequency === "monthly") body["day_of_month"] = dayOfMonth;
    // ``delivery_to`` is server-derived from channel +
    // operator.tgid; the form does not send it.
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
    return body;
  }
  async function save() {
    setError(null);
    if (!name.trim() || !prompt.trim()) { setError("名称 和 prompt 不能为空"); return; }
    if (frequency === "weekly" && (dayOfWeek < 0 || dayOfWeek > 6)) { setError("请选择星期"); return; }
    if (frequency === "monthly" && (dayOfMonth < 1 || dayOfMonth > 31)) { setError("请选择 1-31"); return; }
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) { setError("小时必须 0-23"); return; }
    if (!Number.isInteger(minute) || minute < 0 || minute > 59) { setError("分钟必须 0-59"); return; }
    if (frequency === "once" && !runAt) { setError("请选择触发时间"); return; }
    const body = toBody();
    try {
      if (props.taskId === null) {
        await createMut.mutateAsync(body);
      } else {
        await updateMut.mutateAsync({ taskId: props.taskId, payload: body });
      }
      props.onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    }
  }

  const saving = createMut.isPending || updateMut.isPending;

  // HH:MM string helpers (only for daily / weekly / monthly).
  function setHHMM(h: number, m: number) {
    setHour(h);
    setMinute(m);
  }

  return (
    <div className="modal-overlay flex items-center justify-center p-4 overflow-hidden">
      <div className="modal-panel max-w-2xl w-full max-h-[calc(100vh-2rem)] flex flex-col">
        <div className="px-6 py-4 border-b border-border flex items-center justify-between shrink-0">
          <h3 className="text-base font-semibold text-ink">
            {props.taskId ? "编辑任务" : "新建任务"}
          </h3>
          <button
            type="button"
            onClick={props.onClose}
            className="text-ink-3 hover:text-ink text-sm"
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
                id="task-target_channel"
                value={target_channel}
                onChange={(e) => setTargetChannel(e.target.value as "webui" | "tg")}
                className="form-input text-sm py-2 px-3"
              >
                <option value="webui">webui（写到 chat 历史）</option>
                <option value="tg">tg（同时推到 TG）</option>
              </select>
              {/* ``delivery_to`` is no longer a form control:
                  server-derived from channel + operator.
                  channel=webui → "new"; channel=tg → operator's
                  bound tgid (400 if unbound). The cell
                  snippet further down renders the resolved value. */}
            </div>
            <div className="text-xs text-ink-3 self-end pb-2">
              投递目标自动决定：webui 每次新建会话，tg 推到 operator 绑定的 TG chat
            </div>
          </div>

          <p className="text-xs text-ink-3">
            时区和凭据由系统自动决定：cron 用 Settings → 系统时区；凭据用当前登录者（admin 或「被此 MAGI 服务」的 assigned）的 provider / API key。
          </p>

          <label className="flex items-center gap-2 text-sm">
            <Toggle
              checked={enabled}
              onChange={setEnabled}
              ariaLabel="启用（取消勾选 = 停止调度）"
            />
            启用（取消勾选 = 停止调度）
          </label>
          {error && <p className="form-error">✗ {error}</p>}
        </div>
        <div className="px-6 py-4 border-t border-border flex items-center justify-end gap-2">
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


