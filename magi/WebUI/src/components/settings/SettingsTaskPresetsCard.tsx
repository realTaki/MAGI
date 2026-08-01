/**
 * SettingsTaskPresetsCard — manage the preset templates that
 * auto-seed per-user scheduled tasks when a contact becomes
 * ``role='assigned'`` (see ``magi/proactive/task_presets.py``).
 *
 * One row per preset with:
 *  - key (mono, machine identifier; immutable)
 *  - name (operator-facing label)
 *  - schedule (humanised cron / run_at)
 *  - target_channel badge
 *  - enabled toggle (PATCHes immediately)
 *  - "编辑" button → opens an inline editor
 *
 * Editing a preset does NOT rewrite existing per-user
 * ``Task`` rows (snapshot semantics — see the API module
 * docstring). New assigned contacts seeded AFTER the edit
 * pick up the new config; existing ones keep running under
 * their snapshotted fields. The editor surfaces a caveat
 * banner so the operator isn't surprised by the seeded
 * MAGI's "每日晨报" still having the old prompt after they
 * edit the template.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import { useT } from "../../i18n/index";
import {
  useDeleteTaskPreset,
  useTaskPresets,
  useUpdateTaskPreset,
  type TaskPresetPatch,
  type TaskPresetRow,
} from "../../lib/queries";

// -- helpers --------------------------------------------------------------

function scheduleLabel(p: TaskPresetRow): string {
  if (p.frequency === "once") return `一次性 · ${p.run_at ?? "(未指定)"}`;
  const hhmm = `${String(p.hour).padStart(2, "0")}:${String(p.minute).padStart(2, "0")}`;
  if (p.frequency === "daily") return `每天 ${hhmm}`;
  if (p.frequency === "hourly") return `每小时 ${p.minute} 分`;
  if (p.frequency === "weekly") {
    const dow = p.day_of_week ?? 0;
    const labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
    return `每${labels[dow]} ${hhmm}`;
  }
  if (p.frequency === "monthly") {
    return `每月 ${p.day_of_month ?? 1} 日 ${hhmm}`;
  }
  return p.frequency;
}

// -- editor modal ---------------------------------------------------------

function PresetEditor({
  preset,
  onClose,
}: {
  preset: TaskPresetRow;
  onClose: () => void;
}) {
  const t = useT();
  const updateMut = useUpdateTaskPreset();

  // Local form state — initialise from the preset on
  // mount. Edits land in the PATCH on submit.
  const [name, setName] = useState(preset.name);
  const [description, setDescription] = useState(preset.description);
  const [prompt, setPrompt] = useState(preset.prompt);
  const [frequency, setFrequency] = useState<TaskPresetRow["frequency"]>(preset.frequency);
  const [hour, setHour] = useState(preset.hour);
  const [minute, setMinute] = useState(preset.minute);
  const [dayOfWeek, setDayOfWeek] = useState<number>(preset.day_of_week ?? 0);
  const [dayOfMonth, setDayOfMonth] = useState<number>(preset.day_of_month ?? 1);
  const [runAt, setRunAt] = useState<string>(preset.run_at ?? "");
  const [targetChannel, setTargetChannel] = useState<"webui" | "tg">(preset.target_channel);
  const [enabled, setEnabled] = useState(preset.enabled);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Reset form when the operator opens a different row.
  useEffect(() => {
    setName(preset.name);
    setDescription(preset.description);
    setPrompt(preset.prompt);
    setFrequency(preset.frequency);
    setHour(preset.hour);
    setMinute(preset.minute);
    setDayOfWeek(preset.day_of_week ?? 0);
    setDayOfMonth(preset.day_of_month ?? 1);
    setRunAt(preset.run_at ?? "");
    setTargetChannel(preset.target_channel);
    setEnabled(preset.enabled);
  }, [preset]);

  async function save() {
    setSaveError(null);
    setSaving(true);
    const payload: TaskPresetPatch = {
      name,
      description,
      prompt,
      frequency,
      hour,
      minute,
      day_of_week: frequency === "weekly" ? dayOfWeek : null,
      day_of_month: frequency === "monthly" ? dayOfMonth : null,
      run_at: frequency === "once" ? runAt : null,
      target_channel: targetChannel,
      enabled,
    };
    try {
      await updateMut.mutateAsync({ id: preset.id, payload });
      onClose();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const FREQ_OPTIONS: { value: TaskPresetRow["frequency"]; label: string }[] = [
    { value: "hourly", label: t("settings.taskPresetFrequencyHourly") },
    { value: "daily", label: t("settings.taskPresetFrequencyDaily") },
    { value: "weekly", label: t("settings.taskPresetFrequencyWeekly") },
    { value: "monthly", label: t("settings.taskPresetFrequencyMonthly") },
    { value: "once", label: t("settings.taskPresetFrequencyOnce") },
  ];

  return (
    <div
      className="fixed inset-0 bg-ink/40 flex items-center justify-center p-4 z-50"
      role="dialog"
      aria-modal="true"
    >
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] flex flex-col">
        <div className="px-5 py-3 border-b border-sky-light/40 flex items-center justify-between">
          <h3 className="text-base font-semibold text-ink">
            {t("settings.taskPresetEditTitle")} · <span className="font-mono text-sm text-ink-soft">{preset.key}</span>
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-ink-soft hover:text-ink transition"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        <div className="px-5 py-4 space-y-3 overflow-y-auto">
          {/* Caveat banner — the most important caveat for
              the operator's mental model: edits don't
              rewrite existing per-user tasks. */}
          <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {t("settings.taskPresetEditCaveat")}
          </div>

          <label className="flex flex-col gap-1">
            <span className="form-label">{t("settings.taskPresetColumnName")}</span>
            <input
              className="form-input text-sm py-1.5 px-3"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="form-label">描述</span>
            <textarea
              className="form-input text-sm py-1.5 px-3"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="form-label">{t("settings.taskPresetPromptLabel")}</span>
            <textarea
              className="form-input text-sm py-1.5 px-3 font-mono"
              rows={6}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </label>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 items-end">
            <label className="flex flex-col gap-1 col-span-2">
              <span className="form-label">频率</span>
              <select
                className="form-input text-sm py-1.5 px-3"
                value={frequency}
                onChange={(e) => setFrequency(e.target.value as TaskPresetRow["frequency"])}
              >
                {FREQ_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
            {frequency === "weekly" && (
              <label className="flex flex-col gap-1 col-span-2">
                <span className="form-label">{t("settings.taskPresetDayOfWeekLabel")}</span>
                <select
                  className="form-input text-sm py-1.5 px-3"
                  value={dayOfWeek}
                  onChange={(e) => setDayOfWeek(Number(e.target.value))}
                >
                  {[0, 1, 2, 3, 4, 5, 6].map((d) => (
                    <option key={d} value={d}>{["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d]}</option>
                  ))}
                </select>
              </label>
            )}
            {frequency === "monthly" && (
              <label className="flex flex-col gap-1 col-span-2">
                <span className="form-label">{t("settings.taskPresetDayOfMonthLabel")}</span>
                <input
                  className="form-input text-sm py-1.5 px-3"
                  type="number"
                  min={1}
                  max={31}
                  value={dayOfMonth}
                  onChange={(e) => setDayOfMonth(Number(e.target.value))}
                />
              </label>
            )}
            {frequency !== "once" && (
              <>
                <label className="flex flex-col gap-1">
                  <span className="form-label">{t("settings.taskPresetHourLabel")}</span>
                  <input
                    className="form-input text-sm py-1.5 px-3"
                    type="number"
                    min={0}
                    max={23}
                    value={hour}
                    onChange={(e) => setHour(Number(e.target.value))}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="form-label">{t("settings.taskPresetMinuteLabel")}</span>
                  <input
                    className="form-input text-sm py-1.5 px-3"
                    type="number"
                    min={0}
                    max={59}
                    value={minute}
                    onChange={(e) => setMinute(Number(e.target.value))}
                  />
                </label>
              </>
            )}
            {frequency === "once" && (
              <label className="flex flex-col gap-1 col-span-4">
                <span className="form-label">{t("settings.taskPresetRunAtLabel")}</span>
                <input
                  className="form-input text-sm py-1.5 px-3 font-mono"
                  value={runAt}
                  placeholder="2026-08-01T15:30:00+08:00"
                  onChange={(e) => setRunAt(e.target.value)}
                />
              </label>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 items-end">
            <label className="flex flex-col gap-1">
              <span className="form-label">{t("settings.taskPresetColumnChannel")}</span>
              <select
                className="form-input text-sm py-1.5 px-3"
                value={targetChannel}
                onChange={(e) => setTargetChannel(e.target.value as "webui" | "tg")}
              >
                <option value="webui">webui</option>
                <option value="tg">telegram</option>
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              {enabled ? t("settings.taskPresetEnabled") : t("settings.taskPresetDisabled")}
            </label>
          </div>

          {saveError && (
            <p className="form-error">✗ {saveError}</p>
          )}
        </div>

        <div className="px-5 py-3 border-t border-sky-light/40 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="btn btn-secondary text-sm py-1.5 px-4"
          >
            {t("settings.taskPresetCancelBtn")}
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving || !name.trim() || !prompt.trim()}
            className="btn btn-primary text-sm py-1.5 px-4"
          >
            {saving ? t("settings.agentSaving") : t("settings.taskPresetSaveBtn")}
          </button>
        </div>
      </div>
    </div>
  );
}


// -- main card ------------------------------------------------------------

export function SettingsTaskPresetsCard() {
  const t = useT();
  const presetsQuery = useTaskPresets();
  const updateMut = useUpdateTaskPreset();
  const deleteMut = useDeleteTaskPreset();

  const [editingId, setEditingId] = useState<string | null>(null);
  const presets = presetsQuery.data ?? [];
  const editing = presets.find((p) => p.id === editingId) ?? null;
  const loadError = presetsQuery.error
    ? (presetsQuery.error as Error).message
    : null;

  async function toggleEnabled(p: TaskPresetRow) {
    try {
      await updateMut.mutateAsync({
        id: p.id,
        payload: { enabled: !p.enabled },
      });
    } catch (e) {
      alert((e as Error).message);
    }
  }

  async function onDelete(p: TaskPresetRow) {
    if (!confirm(t("settings.taskPresetDeleteConfirm"))) return;
    try {
      await deleteMut.mutateAsync(p.id);
    } catch (e) {
      alert((e as Error).message);
    }
  }

  return (
    <ConsoleCard
      title={t("settings.taskPresetsCardTitle")}
      headerRight={
        <InfoTip text={t("settings.taskPresetsCardDesc")} />
      }
    >
      {loadError && <p className="form-error mb-3">✗ {loadError}</p>}

      {presetsQuery.isLoading && presets.length === 0 && (
        <p className="text-sm text-ink-soft">{t("common.loading")}</p>
      )}

      {!presetsQuery.isLoading && presets.length === 0 && !loadError && (
        <p className="text-sm text-ink-soft">{t("settings.taskPresetsEmpty")}</p>
      )}

      {presets.length > 0 && (
        <div className="overflow-x-auto">
          <table className="data-table w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-3 font-medium">{t("settings.taskPresetColumnKey")}</th>
                <th className="py-2 pr-3 font-medium">{t("settings.taskPresetColumnName")}</th>
                <th className="py-2 pr-3 font-medium">{t("settings.taskPresetColumnSchedule")}</th>
                <th className="py-2 pr-3 font-medium">{t("settings.taskPresetColumnChannel")}</th>
                <th className="py-2 pr-3 font-medium">{t("settings.taskPresetColumnStatus")}</th>
                <th className="py-2 font-medium w-32 text-right">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {presets.map((p) => (
                <tr
                  key={p.id}
                  className="border-b border-sky-light/30 last:border-0 hover:bg-sky-pale/10 transition-colors"
                >
                  <td className="py-2 pr-3 font-mono text-xs">{p.key}</td>
                  <td className="py-2 pr-3">
                    <div className="font-medium text-ink">{p.name}</div>
                    {p.description && (
                      <div className="text-[11px] text-ink-soft mt-0.5 line-clamp-1">{p.description}</div>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-xs text-ink-soft font-mono">
                    {scheduleLabel(p)}
                  </td>
                  <td className="py-2 pr-3 text-xs">
                    <span className="inline-block rounded bg-sky-pale/40 text-ink-soft text-[10px] font-mono px-1.5 py-0.5">
                      {p.target_channel}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-xs">
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        className="sr-only peer"
                        checked={p.enabled}
                        onChange={() => toggleEnabled(p)}
                      />
                      <div className="w-8 h-5 bg-ink-soft/20 rounded-full peer peer-checked:bg-ocean peer-focus:ring-2 peer-focus:ring-sky-300 transition-colors after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-transform peer-checked:after:translate-x-3" />
                    </label>
                  </td>
                  <td className="py-2 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => setEditingId(p.id)}
                        title={t("common.edit")}
                        className="px-2 py-1 rounded text-sky-700 hover:text-sky-deep hover:bg-sky-pale/40 transition text-xs"
                      >
                        {t("common.edit")}
                      </button>
                      <button
                        type="button"
                        onClick={() => onDelete(p)}
                        title={t("settings.taskPresetDeleteBtn")}
                        className="px-2 py-1 rounded text-rose-700 hover:text-rose-900 hover:bg-rose-50 transition text-xs"
                      >
                        {t("settings.taskPresetDeleteBtn")}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <PresetEditor
          preset={editing}
          onClose={() => setEditingId(null)}
        />
      )}
    </ConsoleCard>
  );
}
