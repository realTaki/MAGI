/**
 * SettingsCompactCard — auto-compact config (D.17).
 *
 * Three knobs the operator dials for the
 * auto-compaction behaviour:
 *
 * - context_window  : how many tokens the model can
 *                      take (default 100000 = Minimax M2.7 spec)
 * - threshold_pct   : at what %% of context_window to
 *                      trigger a compaction pass
 * - keep_recent     : after compaction, how many of
 *                      the most-recent original messages
 *                      stay in the active list (in
 *                      addition to the summary at
 *                      messages[0])
 *
 * All three are server-validated; the form mirrors the
 * server-side bounds (min/max on the input element +
 * a sanity check before send) so an operator typing
 * outside the range gets a fast client-side error
 * instead of a round-trip + 422.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { useT } from "../../i18n/index";

export function SettingsCompactCard() {
  const t = useT();
  type CompactOut = {
    context_window: number;
    threshold_pct: number;
    keep_recent: number;
    default_context_window: number;
    default_threshold_pct: number;
    default_keep_recent: number;
  };

  const [data, setData] = useState<CompactOut | null>(null);
  const [contextWindow, setContextWindow] = useState<string>("");
  const [thresholdPct, setThresholdPct] = useState<string>("");
  const [keepRecent, setKeepRecent] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoadError(null);
    try {
      const r = await fetch("/api/system-settings/compact-config", {
        credentials: "include",
      });
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      const body = (await r.json()) as CompactOut;
      setData(body);
      setContextWindow(String(body.context_window));
      setThresholdPct(String(body.threshold_pct));
      setKeepRecent(String(body.keep_recent));
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const dirty =
    data !== null &&
    (Number(contextWindow) !== data.context_window ||
      Number(thresholdPct) !== data.threshold_pct ||
      Number(keepRecent) !== data.keep_recent);

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    const cw = Number(contextWindow);
    const tp = Number(thresholdPct);
    const kr = Number(keepRecent);
    if (!Number.isInteger(cw) || !Number.isInteger(tp) || !Number.isInteger(kr)) {
      setSaveError("三个值必须是整数");
      return;
    }
    if (data !== null) {
      if (cw < 16000 || cw > 200000) {
        setSaveError("context_window 必须介于 16000 与 200000 之间");
        return;
      }
      if (tp < 50 || tp > 95) {
        setSaveError("threshold_pct 必须介于 50 与 95 之间");
        return;
      }
      if (kr < 5 || kr > 100) {
        setSaveError("keep_recent 必须介于 5 与 100 之间");
        return;
      }
    }
    setSaving(true);
    try {
      const r = await fetch("/api/system-settings/compact-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          context_window: cw,
          threshold_pct: tp,
          keep_recent: kr,
        }),
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        setSaveError(body.detail ?? `Save failed (${r.status})`);
        return;
      }
      const body = (await r.json()) as CompactOut;
      setData(body);
      setContextWindow(String(body.context_window));
      setThresholdPct(String(body.threshold_pct));
      setKeepRecent(String(body.keep_recent));
      setSavedNotice(
        "已保存。下一条消息生效（正在进行的 chat 用旧值）。",
      );
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard title={t("settings.autoCompact")}>
      <p className="text-sm text-ink-soft">
        长 session 在消息数累积到 context_window × threshold_pct% 时触发压缩:
        老的 N 条调 LLM 生成 summary (写到 messages[0])、原文进 archive、
        active 只留最近 keep_recent 条。
      </p>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {!loadError && data && (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="form-label">Context window</label>
              <input
                type="number"
                min={16000}
                max={200000}
                step={1000}
                value={contextWindow}
                onChange={(e) => setContextWindow(e.target.value)}
                className="form-input text-sm font-mono py-2 px-3 w-full"
              />
              <p className="mt-1 text-xs text-ink-soft">
                默认 {data.default_context_window.toLocaleString()}
              </p>
            </div>
            <div>
              <label className="form-label">Threshold (%)</label>
              <input
                type="number"
                min={50}
                max={95}
                step={1}
                value={thresholdPct}
                onChange={(e) => setThresholdPct(e.target.value)}
                className="form-input text-sm font-mono py-2 px-3 w-full"
              />
              <p className="mt-1 text-xs text-ink-soft">
                默认 {data.default_threshold_pct}
              </p>
            </div>
            <div>
              <label className="form-label">Keep recent</label>
              <input
                type="number"
                min={5}
                max={100}
                step={1}
                value={keepRecent}
                onChange={(e) => setKeepRecent(e.target.value)}
                className="form-input text-sm font-mono py-2 px-3 w-full"
              />
              <p className="mt-1 text-xs text-ink-soft">
                默认 {data.default_keep_recent}
              </p>
            </div>
          </div>
          {(data.context_window !== data.default_context_window ||
            data.threshold_pct !== data.default_threshold_pct ||
            data.keep_recent !== data.default_keep_recent) && (
            <p className="text-xs text-ink-soft">
              当前生效值 {data.context_window.toLocaleString()} / {data.threshold_pct}% / keep {data.keep_recent}
            </p>
          )}
        </div>
      )}

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && (
        <p className="mt-3 text-xs text-emerald-700">✓ {savedNotice}</p>
      )}

      <div className="flex items-center gap-2 pt-3 mt-3 border-t border-sky-light/40">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty}
          className="btn btn-primary text-sm py-1.5 px-4"
          title={!dirty ? "没有改动" : "保存"}
        >
          {saving ? "保存中…" : "保存"}
        </button>
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setContextWindow(String(data?.context_window ?? ""));
              setThresholdPct(String(data?.threshold_pct ?? ""));
              setKeepRecent(String(data?.keep_recent ?? ""));
              setSaveError(null);
              setSavedNotice(null);
            }}
            disabled={saving}
            className="btn btn-ghost text-sm py-1.5 px-3"
          >
            放弃改动
          </button>
        )}
      </div>
    </ConsoleCard>
  );
}

