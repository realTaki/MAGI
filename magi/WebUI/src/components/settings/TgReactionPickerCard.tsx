/**
 * TgReactionPickerCard — shared radio-group card used by
 * both the read-reaction and done-reaction settings. They
 * hit the same backend allowlist
 * (``magi.channels.telegram.config.REACTION_CHOICES``) so
 * the UI shape is identical; only the endpoint URL and
 * title differ.
 *
 * Why two endpoints but one component:
 * - The backend keeps the two reactions separate so a Save
 *   doesn't accidentally clear the other.
 * - The frontend wants them on separate Settings sidebar
 *   rows so the operator can grok "what each emoji means"
 *   without reading a tool-tip.
 *
 * The endpoint URL is required because the read endpoint
 * is ``/api/tg-settings/read-reaction`` and the done one
 * is ``/api/tg-settings/done-reaction``; both use the same
 * response + request shape.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { useT } from "../../i18n/index";

export type ReactionOut = {
  current: string;
  default: string;
  choices: { value: string; label: string }[];
};

export function TgReactionPickerCard(props: {
  /** Card title — already-resolved i18n string. */
  title: string;
  /** Card body — explains what this emoji is for. */
  description: string;
  /** Endpoint URL, e.g. ``/api/tg-settings/read-reaction``. */
  endpoint: string;
  /** Saved-toast copy — already-resolved. */
  savedNotice: string;
}) {
  const t = useT();
  const [data, setData] = useState<ReactionOut | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoadError(null);
    try {
      const r = await fetch(props.endpoint, {
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
    // Reload when the endpoint changes (sidebar swap between
    // read-reaction and done-reaction). Without this dep
    // the second card would render the first card's state
    // because the component identity stays the same across
    // mount cycles driven by the parent's ``section`` flag.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.endpoint]);

  const dirty = data !== null && picked !== data.current;

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    setSaving(true);
    try {
      const r = await fetch(props.endpoint, {
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
      setSavedNotice(props.savedNotice);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard title={props.title}>
      <p className="text-sm text-ink-soft">{props.description}</p>

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
                  name={`tg-reaction-${props.endpoint}`}
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
              {t("settings.tgReactionDefaultHint")}
              <span className="font-mono ml-1">{data.default}</span>
              {t("settings.tgReactionDefaultHintTail")}
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
            {t("settings.discardChanges")}
          </button>
        )}
      </div>
    </ConsoleCard>
  );
}