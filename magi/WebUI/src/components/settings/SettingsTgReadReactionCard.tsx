/**
 * SettingsTgReadReactionCard — pick the emoji the EVE
 * bot stamps on each incoming TG message via
 * ``set_message_reaction`` as a "seen, working on it" signal
 * that fires before the LLM call so the user sees it
 * instantly even if the reply takes 30s.
 *
 * Save hits ``PUT /api/tg-settings/read-reaction`` and
 * takes effect on the *next* inbound TG message; no
 * restart, no reload. The backend allowlists 5 emoji
 * (see ``magi.channels.telegram.config.REACTION_CHOICES``);
 * anything the API returns is one of those, so the
 * radio rows are guaranteed to round-trip.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { useT } from "../../i18n/index";

export function SettingsTgReadReactionCard() {
  const t = useT();
  type ReactionChoice = { value: string; label: string };
  type ReactionOut = {
    current: string;
    default: string;
    choices: ReactionChoice[];
  };

  const [data, setData] = useState<ReactionOut | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Read-only mode when the bot isn't connected (no
  // outgoing reactions will fire anyway). We surface the
  // Channels card's data via the same ``props.data`` path
  // the persona card uses, but the helper just reads
  // ``bot.username`` — keep it minimal.
  async function load() {
    setLoadError(null);
    try {
      const r = await fetch("/api/tg-settings/read-reaction", {
        credentials: "include",
      });
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      const body = (await r.json()) as ReactionOut;
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
      const r = await fetch("/api/tg-settings/read-reaction", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ emoji: picked }),
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
      const body = (await r.json()) as ReactionOut;
      setData(body);
      setPicked(body.current);
      setSavedNotice("已保存。下一条 TG 消息就会用新的 emoji。");
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard title={t("settings.tgReadEmoji")}>
      <p className="text-sm text-ink-soft">
        员工给 EVE bot 发消息时，bot 给那条消息加一个 emoji 作为「已收到 / 在处理」的信号。
        改完保存即生效，下一条消息就生效。
      </p>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {!loadError && data && (
        <div className="mt-4 space-y-2">
          {data.choices.map((c) => {
            const selected = picked === c.value;
            return (
              <label
                key={c.value}
                className={
                  "flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer transition " +
                  (selected
                    ? "border-sky-deep bg-sky-pale/40"
                    : "border-sky-light/40 hover:bg-sky-pale/20")
                }
              >
                <input
                  type="radio"
                  name="tg-read-reaction"
                  value={c.value}
                  checked={selected}
                  onChange={() => setPicked(c.value)}
                  className="accent-sky-deep"
                />
                <span className="text-sm text-ink">{c.label}</span>
              </label>
            );
          })}
          {data.default !== data.current && (
            <p className="text-xs text-ink-soft mt-1">
              未配置时用默认 <span className="font-mono">{data.default}</span>。
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

// -- system timezone card --------------------------------------------------
//
// D.15 — the timezone this MAGI node uses for "natural
// week" / "natural month" bucket boundaries. The token
// bill aggregation endpoint reads the same value on every
// call, so a Save here is immediately reflected in the
// next ``GET /api/employees/{id}/token-usage``.
//
// The dropdown lists the full IANA tz database
// (zoneinfo.available_timezones()) sorted alphabetically.
// v0 doesn't have a region-grouped preferences panel —
// the alphabetical list is uniform and works for any
// locale. The backend rejects unknown tz with 400 so a
// stale client doesn't get a silent fall-back to UTC.
