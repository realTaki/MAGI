/**
 * SettingsSystemTimezoneCard — IANA timezone picker.
 *
 * The timezone this MAGI node uses for "natural week" /
 * "natural month" bucket boundaries. Read by the
 * token-bill aggregation endpoint on every call, so a
 * Save here is immediately reflected in the next
 * ``GET /api/employees/{id}/token-usage``.
 *
 * The dropdown lists the full IANA tz database
 * (``zoneinfo.available_timezones()``) sorted
 * alphabetically. v0 doesn't have a region-grouped
 * preferences panel — the alphabetical list is uniform
 * and works for any locale. The backend rejects
 * unknown tz with 400 so a stale client doesn't get
 * a silent fall-back to UTC.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { useT } from "../../i18n/index";

export function SettingsSystemTimezoneCard() {
  const t = useT();
  type TzOut = {
    current: string;
    default: string;
    choices: string[];
  };

  const [data, setData] = useState<TzOut | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoadError(null);
    try {
      const r = await fetch("/api/system-settings/timezone", {
        credentials: "include",
      });
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      const body = (await r.json()) as TzOut;
      setData(body);
      setPicked(body.current);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const dirty = data !== null && picked !== data.current;

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    setSaving(true);
    try {
      const r = await fetch("/api/system-settings/timezone", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timezone: picked }),
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          code?: string;
          detail?: string;
        };
        setSaveError(body.detail ?? `Save failed (${r.status})`);
        return;
      }
      const body = (await r.json()) as TzOut;
      setData(body);
      setPicked(body.current);
      setSavedNotice("已保存。下次 token 用量查询就用新时区。");
      // (saved notice remains in zh for v0; localized copy
      // lands when we extract a setting-specific notice key.)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard title={t("settings.timezone")}>
      <p className="text-sm text-ink-soft">
        {t("settings.timezoneDesc")}
      </p>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {!loadError && data && (
        <div className="mt-4 space-y-2">
          <select
            value={picked}
            onChange={(e) => setPicked(e.target.value)}
            className="form-input text-sm py-2 px-3 w-full sm:w-auto"
          >
            {data.choices.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </select>
          {data.default !== data.current && (
            <p className="text-xs text-ink-soft">
              未设置时用默认 <span className="font-mono">{data.default}</span>。
            </p>
          )}
        </div>
      )}

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && <p className="mt-3 text-xs text-emerald-700">✓ {savedNotice}</p>}

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
              setPicked(data?.current ?? "");
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

// -- tool-loop max iterations card ----------------------------------------
//
// D.16 — caps how many LLM ↔ tool cycles one chat turn
// can run. The agent loop reads this on every inbound chat
// and aborts past the limit (with a fallback reply). Each
// iteration is one round-trip + tool execution, so the cap
// also bounds the wall-clock cost of one turn.
//
// Bound is enforced server-side in
// ``magi.channels.webui.api.system_settings`` (MIN=1 MAX=50);
// the form here mirrors those bounds so the operator can't
// even type a value that the API would 422 on.
