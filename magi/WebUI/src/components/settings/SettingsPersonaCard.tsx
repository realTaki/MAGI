/**
 * SettingsPersonaCard — edit the workspace ``SOUL.md``.
 *
 * Single workspace-wide persona for v0 (per-MAGI
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
 *
 * Migrated to react-query: ``useSoul`` is the cached
 * server baseline. On first load it seeds both
 * ``savedContent`` and ``draftContent``; subsequent
 * refetches (after Save / Reset) only update the
 * ``savedContent`` baseline — the operator's in-flight
 * edits in ``draftContent`` are left alone so the dirty
 * flag stays meaningful.
 */

import { useEffect, useRef, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import Notice from "../Notice";
import { useT } from "../../i18n/index";
import { useResetSoul, useSoul, useUpdateSoul } from "../../lib/queries";

export function SettingsPersonaCard() {
  const t = useT();
  const soulQuery = useSoul();
  const updateMut = useUpdateSoul();
  const resetMut = useResetSoul();

  // Single textarea. The loaded value IS the editable value:
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
  const [draftContent, setDraftContent] = useState<string>("");
  const [savedContent, setSavedContent] = useState<string>("");
  const [modifiedAt, setModifiedAt] = useState<string | null>(null);
  const [isFallback, setIsFallback] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);

  // 8 KB cap mirrors the backend's
  // ``magi.channels.api.soul._MAX_SOUL_CHARS``.
  const SOUL_MAX = 8000;
  // Warning at 80% so the operator gets a visual cue before
  // the textarea overflows the layout.
  const SOUL_WARN = SOUL_MAX * 0.8;
  const chars = draftContent.length;
  const overLimit = chars > SOUL_MAX;
  const nearLimit = chars > SOUL_WARN;
  const dirty = draftContent !== savedContent;

  // Track the last server content we hydrated the draft
  // from. The ref lets the next ``useEffect`` skip the
  // hydrate when the cache refetch is the SAME content
  // we already have (e.g. ``refetchOnWindowFocus``).
  const lastHydratedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!soulQuery.data) return;
    const incoming = soulQuery.data.content;
    if (lastHydratedRef.current === incoming) return;
    lastHydratedRef.current = incoming;
    // Always update the baseline so the dirty flag
    // reflects the latest server value. The draft is
    // seeded only on the first hydration; on later
    // refetches (e.g. after save / reset invalidates
    // the cache) we leave the draft alone so the
    // operator's in-flight edits aren't blown away.
    setSavedContent(incoming);
    setModifiedAt(soulQuery.data.modified_at);
    setIsFallback(soulQuery.data.is_bundled_fallback);
    if (draftContent === "" && savedContent === "") {
      setDraftContent(incoming);
    }
  }, [soulQuery.data, draftContent, savedContent]);

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    const trimmed = draftContent.trim();
    if (!trimmed) {
      setSaveError("Persona 内容不能为空（空白不算）");
      return;
    }
    try {
      const data = await updateMut.mutateAsync(trimmed);
      setSavedContent(trimmed);
      setDraftContent(trimmed);
      setModifiedAt(data.modified_at);
      setIsFallback(false);
      // Mark the hydration as up-to-date so the
      // refetch the mutation triggers doesn't clobber
      // the now-stable draft with the same content.
      lastHydratedRef.current = trimmed;
      setSavedNotice(t("persona.savedNotice"));
    } catch (err) {
      // ``apiFetch`` already throws with the server's
      // ``detail`` message; surface it directly.
      setSaveError(err instanceof Error ? err.message : t("settings.networkError"));
    }
  }

  async function resetToDefault() {
    if (!confirm(t("persona.resetConfirm"))) {
      return;
    }
    setSaveError(null);
    setSavedNotice(null);
    try {
      await resetMut.mutateAsync();
      // The mutation invalidates ``qk.soul``; the
      // refetch will hydrate the draft via the
      // useEffect above.
      setSavedNotice(t("persona.resetNotice"));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : t("settings.networkError"));
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

  const saving = updateMut.isPending;
  const resetting = resetMut.isPending;
  const loadError = soulQuery.error
    ? (soulQuery.error as Error).message
    : null;

  return (
    <ConsoleCard
      title={t("persona.title")}
      headerRight={<InfoTip text={t("persona.description") + " " + t("persona.draftHint")} />}
    >
      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {isFallback && !loadError && (
        <Notice tone="warning">{t("persona.fallbackBanner")}</Notice>
      )}

      {/* Single editable textarea.
          ``rows={14}`` + ``min/maxHeight`` give a comfortable
          multi-line editing surface that doesn't push the
          Save button off-screen on long personas. The
          "未保存" marker floats to the right when ``dirty``
          is true, so the operator always knows whether
          their last edit has been committed. */}
      <div className="mt-4">
        {dirty && (
          <p className="text-[10px] text-warning mb-1">{t("persona.dirty")}</p>
        )}
        <textarea
          value={draftContent}
          onChange={(e) => setDraftContent(e.target.value)}
          rows={14}
          spellCheck={false}
          className={
            "mt-2 form-input w-full text-sm font-mono leading-relaxed py-2 px-3 resize-y " +
            (overLimit ? "border-danger focus:border-danger" : "")
          }
          style={{ minHeight: "260px", maxHeight: "520px" }}
        />
        <div className="flex items-center justify-between text-xs mt-1">
          <span
            className={
              overLimit
                ? "text-danger font-medium"
                : nearLimit
                  ? "text-warning"
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
      {savedNotice && <p className="mt-3 text-xs text-success">✓ {savedNotice}</p>}

      <div className="flex items-center gap-2 pt-3 mt-3 border-t border-border">
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
