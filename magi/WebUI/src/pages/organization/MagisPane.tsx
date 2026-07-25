/**
 * MagisPane — the "智能体管理 / magis 管理" half of the Organization tab.
 *
 * Manages individual :class:`Magi` rows (MAGI runtime agents bound
 * to a MAGIC team). The MAGIC team tree is managed separately in
 * :file:`MagicsPane.tsx`.
 *
 * Features:
 *   - Flat table of all Magis with position badges + provider/key info.
 *   - Create form (team picker from MAGICs, position, provider, api_key).
 *   - Delete with confirmation.
 *   - Per-team breakdown summary.
 *
 * Reads/writes ``GET/POST/DELETE /api/magis`` + reads ``GET /api/magics``
 * (for the team-name column and the create-form picker).
 */

import { useEffect, useMemo, useState } from "react";

import ConsoleCard from "../../components/ConsoleCard";
import { useT } from "../../i18n/index";
import type { MAGICRow } from "./MagicsPane";

// -- backend wire shapes ----------------------------------------------------

type MagiRow = {
  id: number;
  magic_id: number;
  magic_position: string; // "adam" | "eve"
  provider: string | null;
  api_key_set: boolean;
  api_key_last4: string | null;
  created_at: string;
  updated_at: string;
};

// -- pane -------------------------------------------------------------------

export function MagisPane() {
  const t = useT();
  const [magics, setMagics] = useState<MAGICRow[] | null>(null);
  const [magis, setMagis] = useState<MagiRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Create form state.
  const [addForm, setAddForm] = useState<{
    magic_id: string;
    magic_position: "adam" | "eve";
    provider: string;
    api_key: string;
  }>({ magic_id: "", magic_position: "eve", provider: "", api_key: "" });
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const reload = async () => {
    setLoadError(null);
    try {
      const [mr, gr] = await Promise.all([
        fetch("/api/magics", { credentials: "include" }),
        fetch("/api/magis", { credentials: "include" }),
      ]);
      if (!mr.ok || !gr.ok) {
        setLoadError(`load failed (magics=${mr.status}, magis=${gr.status})`);
        return;
      }
      setMagics((await mr.json()) as MAGICRow[]);
      setMagis((await gr.json()) as MagiRow[]);
    } catch (e) {
      setLoadError(`network error: ${(e as Error).message}`);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  // Team name lookup.
  const teamName = useMemo(() => {
    const m = new Map<number, string>();
    (magics ?? []).forEach((c) => m.set(c.id, c.name));
    return m;
  }, [magics]);

  // Group magis by magic_id for the breakdown.
  const magisByMagic = useMemo(() => {
    const out = new Map<number, MagiRow[]>();
    (magis ?? []).forEach((m) => {
      const list = out.get(m.magic_id) ?? [];
      list.push(m);
      out.set(m.magic_id, list);
    });
    return out;
  }, [magis]);

  const submitCreate = async () => {
    setAddError(null);
    const magicIdNum = Number.parseInt(addForm.magic_id, 10);
    if (!Number.isFinite(magicIdNum) || magicIdNum <= 0) {
      setAddError("magic_id must be a positive integer");
      return;
    }
    setAdding(true);
    try {
      const res = await fetch("/api/magis", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          magic_id: magicIdNum,
          magic_position: addForm.magic_position,
          provider: addForm.provider || null,
          api_key: addForm.api_key || null,
        }),
      });
      if (!res.ok) {
        const body = await res.text();
        setAddError(`create failed: ${res.status} ${body}`);
        return;
      }
      setAddForm({
        magic_id: "",
        magic_position: "eve",
        provider: "",
        api_key: "",
      });
      await reload();
    } catch (e) {
      setAddError(`network error: ${(e as Error).message}`);
    } finally {
      setAdding(false);
    }
  };

  const deleteMagi = async (id: number) => {
    if (!confirm(t("magis.deleteConfirm") || `Delete magi #${id}?`)) return;
    const res = await fetch(`/api/magis/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (res.ok) await reload();
  };

  return (
    <div className="space-y-4">
      <ConsoleCard title={t("magis.paneTitle")}>
        {loadError !== null && (
          <p className="text-sm text-red-600 mb-3">{loadError}</p>
        )}

        {/* Magis table */}
        <h3 className="text-sm font-medium text-ink mb-2">
          {t("magis.magisHeading")}
        </h3>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-ink-soft border-b border-ink-soft/20">
                <th className="py-2 pr-3">{t("magis.columnId")}</th>
                <th className="py-2 pr-3">{t("magis.columnTeam")}</th>
                <th className="py-2 pr-3">{t("magis.columnPosition")}</th>
                <th className="py-2 pr-3">{t("magis.columnProvider")}</th>
                <th className="py-2 pr-3">{t("magis.columnApiKey")}</th>
                <th className="py-2 pr-3">{t("magis.columnActions")}</th>
              </tr>
            </thead>
            <tbody>
              {(magis ?? []).map((m) => (
                <tr key={m.id} className="border-b border-ink-soft/10">
                  <td className="py-1.5 pr-3 font-mono text-ink-soft">{m.id}</td>
                  <td className="py-1.5 pr-3">
                    <span className="text-ink-soft font-mono text-xs mr-1.5">#{m.magic_id}</span>
                    {teamName.get(m.magic_id) ?? (
                      <span className="text-ink-soft italic">(orphaned)</span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3">
                    <span
                      className={
                        m.magic_position === "adam"
                          ? "px-2 py-0.5 rounded bg-amber-100 text-amber-800 text-xs font-medium"
                          : "px-2 py-0.5 rounded bg-sky-100 text-sky-800 text-xs font-medium"
                      }
                    >
                      {m.magic_position === "adam"
                        ? t("magis.positionAdam")
                        : t("magis.positionEve")}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3">{m.provider ?? "—"}</td>
                  <td className="py-1.5 pr-3 font-mono text-ink-soft text-xs">
                    {m.api_key_set
                      ? `…${m.api_key_last4 ?? ""}`
                      : t("magis.keyNotSet")}
                  </td>
                  <td className="py-1.5 pr-3">
                    <button
                      type="button"
                      onClick={() => void deleteMagi(m.id)}
                      className="text-xs text-red-600 hover:underline"
                    >
                      {t("common.delete")}
                    </button>
                  </td>
                </tr>
              ))}
              {magis !== null && magis.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-4 text-ink-soft text-center">
                    {t("magis.empty")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Per-team breakdown */}
        <h3 className="text-sm font-medium text-ink mt-6 mb-2">
          {t("magis.breakdownHeading")}
        </h3>
        <ul className="text-sm text-ink-soft space-y-0.5">
          {Array.from(magisByMagic.entries()).map(([mid, list]) => (
            <li key={mid}>
              <span className="font-mono text-xs">
                {teamName.get(mid) ?? `#${mid}`}
              </span>
              {" — "}
              {list.length} agent{list.length === 1 ? "" : "s"}
              {" ("}
              {list.map((x) => x.magic_position).join(", ")}
              {")"}
            </li>
          ))}
          {magisByMagic.size === 0 && (
            <li>{t("magis.breakdownEmpty")}</li>
          )}
        </ul>

        {/* Create form */}
        <h3 className="text-sm font-medium text-ink mt-6 mb-2">
          {t("magis.createHeading")}
        </h3>
        {addError !== null && (
          <p className="text-sm text-red-600 mb-2">{addError}</p>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-5 gap-2 items-end">
          <label className="text-xs text-ink-soft">
            {t("magis.createTeamLabel")}
            <select
              className="mt-1 block w-full rounded border border-ink-soft/30 px-2 py-1 text-sm"
              value={addForm.magic_id}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, magic_id: e.target.value }))
              }
            >
              <option value="" disabled>
                —
              </option>
              {(magics ?? []).map((c) => (
                <option key={c.id} value={String(c.id)}>
                  {c.name} (#{c.id})
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-ink-soft">
            {t("magis.createPositionLabel")}
            <select
              className="mt-1 block w-full rounded border border-ink-soft/30 px-2 py-1 text-sm"
              value={addForm.magic_position}
              onChange={(e) =>
                setAddForm((f) => ({
                  ...f,
                  magic_position: e.target.value as "adam" | "eve",
                }))
              }
            >
              <option value="eve">EVE</option>
              <option value="adam">ADAM</option>
            </select>
          </label>
          <label className="text-xs text-ink-soft">
            {t("magis.createProviderLabel")}
            <input
              className="mt-1 block w-full rounded border border-ink-soft/30 px-2 py-1 text-sm"
              placeholder="anthropic / openai / minimax"
              value={addForm.provider}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, provider: e.target.value }))
              }
            />
          </label>
          <label className="text-xs text-ink-soft">
            {t("magis.createApiKeyLabel")}
            <input
              type="password"
              className="mt-1 block w-full rounded border border-ink-soft/30 px-2 py-1 text-sm"
              value={addForm.api_key}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, api_key: e.target.value }))
              }
            />
          </label>
          <button
            type="button"
            disabled={adding || !addForm.magic_id}
            className="rounded bg-ink text-paper px-3 py-1.5 text-sm disabled:opacity-50"
            onClick={() => void submitCreate()}
          >
            {adding ? "…" : t("common.add")}
          </button>
        </div>
      </ConsoleCard>
    </div>
  );
}
