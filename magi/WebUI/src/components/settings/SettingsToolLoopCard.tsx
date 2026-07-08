/**
 * SettingsToolLoopCard — agent loop tool-iteration cap.
 *
 * Caps how many LLM ↔ tool cycles one chat turn can run.
 * The agent loop reads this on every inbound chat and
 * aborts past the limit (with a fallback reply). Each
 * iteration is one round-trip + tool execution, so
 * the cap also bounds the wall-clock cost of one turn.
 *
 * Bounds enforced server-side in
 * ``magi.channels.webui.api.system_settings``
 * (MIN=1, MAX=50); the form mirrors those bounds so
 * the operator can't even type a value the API would
 * 422 on.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { useT } from "../../i18n/index";

export function SettingsToolLoopCard() {
  const t = useT();
  type IterationsOut = {
    current: number;
    default: number;
    min: number;
    max: number;
  };

  const [data, setData] = useState<IterationsOut | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoadError(null);
    try {
      const r = await fetch(
        "/api/system-settings/tool-max-iterations",
        { credentials: "include" },
      );
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      const body = (await r.json()) as IterationsOut;
      setData(body);
      setPicked(String(body.current));
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const dirty =
    data !== null && Number(picked) !== data.current && picked !== "";

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    const value = Number(picked);
    if (!Number.isInteger(value)) {
      setSaveError("必须是整数");
      return;
    }
    if (data !== null && (value < data.min || value > data.max)) {
      setSaveError(`必须介于 ${data.min} 和 ${data.max} 之间`);
      return;
    }
    setSaving(true);
    try {
      const r = await fetch(
        "/api/system-settings/tool-max-iterations",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
          credentials: "include",
        },
      );
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        setSaveError(body.detail ?? `Save failed (${r.status})`);
        return;
      }
      const body = (await r.json()) as IterationsOut;
      setData(body);
      setPicked(String(body.current));
      setSavedNotice(
        "已保存。下一条消息生效（正在进行的 tool loop 用旧值）。",
      );
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard title={t("settings.toolLoop")}>
      <p className="text-sm text-ink-soft">
        一次对话中，LLM 可以连续调用多少次 tool（read_file / write_file / list_files /
        send_message）才会停。超出后 agent 直接返回 fallback reply。
      </p>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {!loadError && data && (
        <div className="mt-4 space-y-2">
          <div className="flex items-center gap-3">
            <input
              type="number"
              min={data.min}
              max={data.max}
              step={1}
              value={picked}
              onChange={(e) => setPicked(e.target.value)}
              className="form-input text-sm font-mono py-2 px-3 w-24"
            />
            <span className="text-xs text-ink-soft">
              范围 {data.min} – {data.max} · 默认 {data.default}
            </span>
          </div>
          {data.default !== data.current && (
            <p className="text-xs text-ink-soft">
              当前生效值 {data.current}。
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
              setPicked(data?.current !== undefined ? String(data.current) : "");
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

// -- compact config card (D.17) --------------------------------------------
//
// Three knobs the operator dials for the auto-compaction
// behaviour:
//
// - context_window  : how many tokens the model can take
//                      (default 100000 = Minimax M2.7 spec)
// - threshold_pct   : at what %% of context_window to
//                      trigger a compaction pass
// - keep_recent     : after compaction, how many of the
//                      most-recent original messages stay
//                      in the active list (in addition to
//                      the summary at messages[0])
//
// All three are server-validated; the form mirrors the
// server-side bounds (min/max on the input element + a
// sanity check before send) so an operator typing outside
// the range gets a fast client-side error instead of a
// round-trip + 422.
