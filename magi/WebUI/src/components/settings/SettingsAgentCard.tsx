/**
 * SettingsAgentCard — merged Agent settings panel.
 *
 * Combines two server-side knobs that govern agent
 * behaviour at runtime into one ConsoleCard so the
 * operator doesn't have to bounce between two
 * sidebar items:
 *
 *   1. **Tool loop max iterations** — caps how many
 *      LLM ↔ tool cycles one chat turn can run.
 *      Server route: ``GET/PUT /api/system-settings/
 *      tool-max-iterations`` (MIN=1, MAX=50).
 *
 *   2. **Auto-compact config** (D.17) — three knobs
 *      that drive the context-window compaction pass:
 *      ``context_window`` / ``threshold_pct`` /
 *      ``keep_recent``. Server route: ``GET/PUT
 *      /api/system-settings/compact-config``.
 *
 * Each sub-section keeps its own state so saving one
 * doesn't dirty the other, and each has its own
 * server-side round-trip — there's no combined PUT.
 * The "Save" button at the bottom of each section is
 * scoped to that section.
 *
 * Migrated to react-query: each section is one
 * ``useQuery`` + one ``useMutation``. The dirty flag
 * stays in local ``useState`` (it's a derived comparison,
 * not a server state).
 */

import { useEffect, useRef, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import { useT } from "../../i18n/index";
import {
  useCompactConfig,
  useToolMaxIterations,
  useUpdateCompactConfig,
  useUpdateToolMaxIterations,
} from "../../lib/queries";

export function SettingsAgentCard() {
  const t = useT();
  return (
    <ConsoleCard
      title={t("settings.agent")}
      headerRight={<InfoTip text={t("settings.agentDesc")} />}
    >
      <div className="mt-6 space-y-8">
        <ToolLoopSection />
        <div className="border-t border-border" />
        <CompactSection />
      </div>
    </ConsoleCard>
  );
}

// -- sub-section: tool loop max iterations ---------------------------------


function ToolLoopSection() {
  const t = useT();
  const query = useToolMaxIterations();
  const updateMut = useUpdateToolMaxIterations();
  const [picked, setPicked] = useState<string>("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const lastHydratedRef = useRef<number | null>(null);

  // First-time hydration: seed the input from the
  // server's ``current`` value. Subsequent refetches
  // (e.g. from window focus) leave the input alone so
  // the operator's in-flight edits aren't clobbered.
  useEffect(() => {
    if (!query.data) return;
    if (lastHydratedRef.current === query.data.current) return;
    lastHydratedRef.current = query.data.current;
    setPicked(String(query.data.current));
  }, [query.data]);

  const data = query.data;
  const dirty = data !== undefined && Number(picked) !== data.current && picked !== "";

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    const value = Number(picked);
    if (!Number.isInteger(value)) {
      setSaveError(t("settings.agentMustBeInteger"));
      return;
    }
    if (data !== undefined && (value < data.min || value > data.max)) {
      setSaveError(`必须介于 ${data.min} 和 ${data.max} 之间`);
      return;
    }
    try {
      const body = await updateMut.mutateAsync(value);
      lastHydratedRef.current = body.current;
      setPicked(String(body.current));
      setSavedNotice(t("settings.agentToolLoopSaved"));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : t("settings.networkError"));
    }
  }

  const loadError = query.error
    ? (query.error as Error).message
    : null;
  const saving = updateMut.isPending;

  return (
    <section>
      <div className="flex items-center gap-1.5">
        <h3 className="text-sm font-medium text-ink">
          {t("settings.toolLoop")}
        </h3>
        <InfoTip text={
          t("settings.toolLoopDesc") + " " +
          (data ? `范围 ${data.min} – ${data.max} · 默认 ${data.default}` : "")
        } />
      </div>

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
        <p className="mt-3 text-xs text-success">✓ {savedNotice}</p>
      )}

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
    </section>
  );
}

// -- sub-section: auto-compact config --------------------------------------


function CompactSection() {
  const t = useT();
  const query = useCompactConfig();
  const updateMut = useUpdateCompactConfig();
  const [contextWindow, setContextWindow] = useState<string>("");
  const [thresholdPct, setThresholdPct] = useState<string>("");
  const [keepRecent, setKeepRecent] = useState<string>("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const lastHydratedRef = useRef<number | null>(null);

  useEffect(() => {
    if (!query.data) return;
    // Use a single fingerprint stamp of all three values
    // so a refetch that returns the same numbers doesn't
    // re-seed the inputs.
    const stamp = query.data.context_window;
    if (lastHydratedRef.current === stamp) return;
    lastHydratedRef.current = stamp;
    setContextWindow(String(query.data.context_window));
    setThresholdPct(String(query.data.threshold_pct));
    setKeepRecent(String(query.data.keep_recent));
  }, [query.data]);

  const data = query.data;
  const dirty =
    data !== undefined &&
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
      setSaveError(t("settings.agentAllMustBeInteger"));
      return;
    }
    if (data !== undefined) {
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
    try {
      const body = await updateMut.mutateAsync({
        context_window: cw,
        threshold_pct: tp,
        keep_recent: kr,
      });
      lastHydratedRef.current = body.context_window;
      setContextWindow(String(body.context_window));
      setThresholdPct(String(body.threshold_pct));
      setKeepRecent(String(body.keep_recent));
      setSavedNotice(t("settings.agentCompactSaved"));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : t("settings.networkError"));
    }
  }

  const loadError = query.error
    ? (query.error as Error).message
    : null;
  const saving = updateMut.isPending;

  return (
    <section>
      <div className="flex items-center gap-1.5">
        <h3 className="text-sm font-medium text-ink">
          {t("settings.autoCompact")}
        </h3>
        <InfoTip text={t("settings.autoCompactDesc")} />
      </div>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {!loadError && data && (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {/*
              Each input is the source of truth — pre-filled
              from the server's current value on load, and re-
              set from ``body.context_window`` after a Save.
              A separate "current effective value" indicator
              was originally rendered below the grid but was
              removed because the boxes already mirror the
              effective value 1:1 (so the line was duplicating
              what the operator was already looking at).
            */}
            <div>
              <div className="flex items-center gap-1.5">
                <label className="form-label">Context window</label>
                <InfoTip text={`默认 ${(data?.default_context_window ?? "").toLocaleString()}`} />
              </div>
              <input
                type="number"
                min={16000}
                max={200000}
                step={1000}
                value={contextWindow}
                onChange={(e) => setContextWindow(e.target.value)}
                className="form-input text-sm font-mono py-2 px-3 w-full"
              />
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <label className="form-label">Threshold (%)</label>
                <InfoTip text={`默认 ${data?.default_threshold_pct ?? ""}`} />
              </div>
              <input
                type="number"
                min={50}
                max={95}
                step={1}
                value={thresholdPct}
                onChange={(e) => setThresholdPct(e.target.value)}
                className="form-input text-sm font-mono py-2 px-3 w-full"
              />
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <label className="form-label">Keep recent</label>
                <InfoTip text={`默认 ${data?.default_keep_recent ?? ""}`} />
              </div>
              <input
                type="number"
                min={5}
                max={100}
                step={1}
                value={keepRecent}
                onChange={(e) => setKeepRecent(e.target.value)}
                className="form-input text-sm font-mono py-2 px-3 w-full"
              />
            </div>
          </div>
        </div>
      )}

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && (
        <p className="mt-3 text-xs text-success">✓ {savedNotice}</p>
      )}

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
    </section>
  );
}
