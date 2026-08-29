/**
 * SettingsSystemTimezoneCard — IANA timezone picker.
 *
 * The timezone this MAGI node uses for "natural week" /
 * "natural month" bucket boundaries. Read by the
 * token-bill aggregation endpoint on every call, so a
 * Save here is immediately reflected in the next
 * ``GET /api/contacts/{uid}/token-usage`` (the per-contact
 * token-usage aggregation; tz affects how the "natural
 * week / month" boundaries are placed).
 *
 * The dropdown lists the full IANA tz database
 * (``zoneinfo.available_timezones()``) sorted
 * alphabetically. v0 doesn't have a region-grouped
 * preferences panel — the alphabetical list is uniform
 * and works for any locale. The backend rejects
 * unknown tz with 400 so a stale client doesn't get
 * a silent fall-back to UTC.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import { useT } from "../../i18n/index";
import { apiFetch, qk } from "../../lib/queryClient";

export function SettingsSystemTimezoneCard() {
  const t = useT();
  const qc = useQueryClient();
  type TzOut = {
    current: string;
    default: string;
    choices: string[];
  };

  const query = useQuery({
    queryKey: qk.systemSettings("timezone"),
    queryFn: () => apiFetch<TzOut>("/api/system-settings/timezone"),
  });
  const data = query.data ?? null;
  const loadError = query.error
    ? (query.error instanceof Error ? query.error.message : t("settings.loadFailed"))
    : null;

  const [picked, setPicked] = useState<string>("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Sync picked with loaded data
  if (data && picked === "") { setPicked(data.current); }

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
        setSaveError(body.detail ?? `${t("common.save")} failed (${r.status})`);
        return;
      }
      const body = (await r.json()) as TzOut;
      void qc.invalidateQueries({ queryKey: qk.systemSettings("timezone") });
      setPicked(body.current);
      setSavedNotice(t("settings.timezoneSavedNotice"));
      // (saved notice remains in zh for v0; localized copy
      // lands when we extract a setting-specific notice key.)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : t("settings.networkError"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard
      title={t("settings.timezone")}
      headerRight={<InfoTip text={
        t("settings.timezoneDesc") + " " +
        (data ? t("settings.timezoneNotSetHint").replace("{tz}", data.default) : "")
      } />}
    >

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
        </div>
      )}

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && <p className="mt-3 text-xs text-success">✓ {savedNotice}</p>}

      <div className="flex items-center gap-2 pt-3 mt-3 border-t border-border">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty}
          className="btn btn-primary text-sm py-1.5 px-4"
          title={!dirty ? t("settings.noChanges") : t("common.save")}
        >
          {saving ? t("settings.agentSaving") : t("common.save")}
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
            {t("settings.discardChanges")}
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
// ``magi.channels.api.system_settings`` (MIN=1 MAX=50);
// the form here mirrors those bounds so the operator can't
// even type a value that the API would 422 on.
