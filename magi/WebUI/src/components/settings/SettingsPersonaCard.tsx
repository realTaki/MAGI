/**
 * SettingsPersonaCard — edit the workspace ``SOUL.md``.
 *
 * Single company-wide persona for v0 (per-employee
 * personas land in C4). One textarea: the operator sees
 * the on-disk content and edits in place; ``Save`` commits,
 * ``Reset`` restores the bundled default. The
 * ``savedContent`` baseline drives the ``dirty`` flag
 * (Save button + revert affordance).
 *
 * Why one textarea (not "view + draft"): until you press
 * Save the textarea is your scratch pad, not the agent's
 * persona — explicit contract, no surprise edits landing
 * in the system prompt.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import { useT } from "../../i18n/index";

export function SettingsPersonaCard() {
  const t = useT();
  // One textarea. The loaded value IS the editable value:
  // the operator sees the on-disk SOUL.md content right away
  // (no separate read-only block) and edits in place. Click
  // Save to commit; click Reset to restore the bundled default.
  //
  // ``savedContent`` is a *baseline* — the value the textarea
  // had immediately after the last load / save / reset. The
  // ``dirty`` flag (``draftContent !== savedContent``) tells
  // us when the operator has unsaved changes and drives the
  // Save button's disabled state + the "放弃改动" revert
  // affordance.
  //
  // Why one textarea instead of "current view + draft" —
  // the operator wants to **see what the agent is using**
  // and **edit it**. Two views force them to translate
  // between "what's in the editor" and "what the agent
  // sees"; one view + Save makes the contract explicit:
  // until you press Save, the textarea is your scratch
  // pad, not the agent's persona.
  const [draftContent, setDraftContent] = useState<string>("");
  const [savedContent, setSavedContent] = useState<string>("");
  const [modifiedAt, setModifiedAt] = useState<string | null>(null);
  const [isFallback, setIsFallback] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);

  // 8 KB cap mirrors the backend's
  // ``magi.channels.webui.api.soul._MAX_SOUL_CHARS``.
  const SOUL_MAX = 8000;
  // Warning at 80% so the operator gets a visual cue before
  // the textarea overflows the layout.
  const SOUL_WARN = SOUL_MAX * 0.8;
  const chars = draftContent.length;
  const overLimit = chars > SOUL_MAX;
  const nearLimit = chars > SOUL_WARN;
  const dirty = draftContent !== savedContent;

  async function load() {
    setLoadError(null);
    try {
      const r = await fetch("/api/soul", { credentials: "include" });
      if (!r.ok) {
        setLoadError(`${t("persona.loadFailed")} (${r.status})`);
        return;
      }
      const data = (await r.json()) as {
        content: string;
        modified_at: string | null;
        is_bundled_fallback: boolean;
      };
      // Both slots collapse to the same value — the
      // textarea shows what's on disk, ``dirty`` is false
      // until the operator types something.
      setSavedContent(data.content);
      setDraftContent(data.content);
      setModifiedAt(data.modified_at);
      setIsFallback(data.is_bundled_fallback);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void load();
    // ``t`` is stable across renders (the i18n context
    // returns a memoised value), so this doesn't refire on
    // locale switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    const trimmed = draftContent.trim();
    if (!trimmed) {
      setSaveError("Persona 内容不能为空（空白不算）");
      return;
    }
    setSaving(true);
    try {
      const r = await fetch("/api/soul", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: trimmed }),
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          code?: string;
          detail?: string;
        };
        setSaveError(body.detail ?? `${t("persona.saveFailed")} (${r.status})`);
        return;
      }
      const data = (await r.json()) as { modified_at: string };
      // Promote the textarea value to "saved" baseline.
      // ``dirty`` flips false; the textarea stays exactly
      // where the operator left it (no need to re-mount).
      setSavedContent(trimmed);
      setDraftContent(trimmed);
      setModifiedAt(data.modified_at);
      setIsFallback(false);
      setSavedNotice(t("persona.savedNotice"));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  async function resetToDefault() {
    if (!confirm(t("persona.resetConfirm"))) {
      return;
    }
    setSaveError(null);
    setSavedNotice(null);
    setResetting(true);
    try {
      const r = await fetch("/api/soul/reset", {
        method: "POST",
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          code?: string;
          detail?: string;
        };
        setSaveError(body.detail ?? `${t("persona.resetFailed")} (${r.status})`);
        return;
      }
      // Re-load so the textarea picks up the canonical
      // truth the backend just wrote.
      await load();
      setSavedNotice(t("persona.resetNotice"));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setResetting(false);
    }
  }

  // ``modifiedAt`` comes back as an ISO UTC string; render a
  // compact "YYYY-MM-DD HH:MM" in local time. Skipped when
  // the persona is the bundled fallback (no mtime yet).
  function formatModified(iso: string | null): string {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      const pad = (n: number) => String(n).padStart(2, "0");
      return (
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
        `${pad(d.getHours())}:${pad(d.getMinutes())}`
      );
    } catch {
      return iso;
    }
  }

  return (
    <ConsoleCard
      title={t("persona.title")}
      headerRight={<InfoTip text={t("persona.description")} />}
    >
      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {isFallback && !loadError && (
        <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {t("persona.fallbackBanner")}
        </div>
      )}

      {/* Single editable textarea.
          ``rows={14}`` + ``min/maxHeight`` give a comfortable
          multi-line editing surface that doesn't push the
          Save button off-screen on long personas. The
          "未保存" marker floats to the right when ``dirty``
          is true, so the operator always knows whether
          their last edit has been committed. */}
      <div className="mt-4">
        <div className="flex items-baseline justify-between">
          <h3 className="text-xs font-medium text-ink-soft uppercase tracking-wide">
            {t("persona.draftLabel")}
          </h3>
          {dirty && (
            <span className="text-[10px] text-amber-700 normal-case tracking-normal">
              · {t("persona.dirty")}
            </span>
          )}
        </div>
        <p className="mt-1 text-[11px] text-ink-soft">
          {t("persona.draftHint")}
        </p>
        <textarea
          value={draftContent}
          onChange={(e) => setDraftContent(e.target.value)}
          rows={14}
          spellCheck={false}
          className={
            "mt-2 form-input w-full text-sm font-mono leading-relaxed py-2 px-3 resize-y " +
            (overLimit ? "border-rose-400 focus:border-rose-500" : "")
          }
          style={{ minHeight: "260px", maxHeight: "520px" }}
        />
        <div className="flex items-center justify-between text-xs mt-1">
          <span
            className={
              overLimit
                ? "text-rose-600 font-medium"
                : nearLimit
                  ? "text-amber-700"
                  : "text-ink-soft"
            }
          >
            {t("persona.charsLine")
              .replace("{chars}", chars.toLocaleString())
              .replace("{max}", SOUL_MAX.toLocaleString())}
            {overLimit && t("persona.overLimitHint")}
          </span>
          {modifiedAt && (
            <span className="text-ink-soft">
              {t("persona.modifiedLabel")}：
              <span className="font-mono">{formatModified(modifiedAt)}</span>
            </span>
          )}
        </div>
      </div>

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && <p className="mt-3 text-xs text-emerald-700">✓ {savedNotice}</p>}

      <div className="flex items-center gap-2 pt-3 mt-3 border-t border-sky-light/40">
        <button
          type="button"
          onClick={save}
          disabled={saving || resetting || !dirty || overLimit}
          className="btn btn-primary text-sm py-1.5 px-4"
          title={
            !dirty
              ? t("persona.dirty")
              : overLimit
                ? t("persona.overLimitHint")
                : t("persona.saveButton")
          }
        >
          {saving ? `${t("persona.saveButton")}…` : t("persona.saveButton")}
        </button>
        <button
          type="button"
          onClick={resetToDefault}
          disabled={saving || resetting}
          className="btn btn-secondary text-sm py-1.5 px-4"
        >
          {resetting ? `${t("persona.resetButton")}…` : t("persona.resetButton")}
        </button>
        {dirty && (
          <button
            type="button"
            onClick={() => {
              // Revert the textarea to the on-disk truth.
              // ``dirty`` flips false; the saved version stays
              // the same so the next comparison is meaningful.
              setDraftContent(savedContent);
              setSaveError(null);
              setSavedNotice(null);
            }}
            disabled={saving || resetting}
            className="btn btn-ghost text-sm py-1.5 px-3"
          >
            {t("persona.discardChanges")}
          </button>
        )}
      </div>
    </ConsoleCard>
  );
}
