/**
 * MagisPane — Agent management.
 */
import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import ConsoleCard from "../../components/ConsoleCard";
import { IconDelete, IconEdit } from "../../components/icons";
import { InfoTip } from "../../components/InfoTip";
import { useT } from "../../i18n/index";
import { qk } from "../../lib/queryClient";
import { useMagics, useMagis, type MagiRow } from "../../lib/queries";

type EditDraft = { name: string; provider: string; api_key: string };

const PROVIDER_OPTIONS = [
  { value: "", label: "—" },
  { value: "claude", label: "Anthropic (Claude)" },
  { value: "minimax-global", label: "Minimax (Global)" },
  { value: "minimax-cn", label: "Minimax (China)" },
];

export function MagisPane() {
  const t = useT();
  const qc = useQueryClient();
  const magicsQuery = useMagics();
  const magisQuery = useMagis();
  const magics = magicsQuery.data ?? [];
  const magis = magisQuery.data ?? [];
  const loadError = (magicsQuery.error || magisQuery.error)
    ? (magicsQuery.error instanceof Error ? magicsQuery.error.message : "") || (magisQuery.error instanceof Error ? magisQuery.error.message : "") || "load failed"
    : null;

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: qk.magics });
    void qc.invalidateQueries({ queryKey: qk.magis });
  };

  const [addForm, setAddForm] = useState({ magic_id: "", name: "", magic_position: "eve" as "adam" | "eve", provider: "", api_key: "" });
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [addOpen, setAddOpen] = useState(false);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft>({ name: "", provider: "", api_key: "" });
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [lifecycleId, setLifecycleId] = useState<number | null>(null);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);

  const teamName = useMemo(() => { const m = new Map<number, string>(); magics.forEach((c) => m.set(c.id, c.name)); return m; }, [magics]);

  const submitCreate = async () => {
    setAddError(null);
    const mid = Number.parseInt(addForm.magic_id, 10);
    if (!Number.isFinite(mid) || mid <= 0) { setAddError("select a council"); return; }
    setAdding(true);
    try {
      const res = await fetch("/api/magis", { method: "POST", credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: addForm.name.trim() || null, magic_id: mid, magic_position: addForm.magic_position, provider: addForm.provider || null, api_key: addForm.api_key || null }) });
      if (!res.ok) { setAddError(`create failed: ${res.status} ${await res.text()}`); return; }
      setAddForm({ magic_id: "", name: "", magic_position: "eve", provider: "", api_key: "" }); setAddOpen(false); refresh();
    } catch (e) { setAddError(`network error: ${(e as Error).message}`); }
    finally { setAdding(false); }
  };

  const startEdit = (m: MagiRow) => { setEditingId(m.id); setEditDraft({ name: m.name ?? "", provider: m.provider ?? "", api_key: "" }); setEditError(null); };
  const cancelEdit = () => { setEditingId(null); setEditError(null); };
  const submitEdit = async (id: number) => {
    setEditError(null);
    const patch: Record<string, unknown> = { name: editDraft.name.trim() || null, provider: editDraft.provider.trim() || null };
    if (editDraft.api_key.trim()) patch.api_key = editDraft.api_key.trim();
    setSaving(true);
    try {
      const res = await fetch(`/api/magis/${id}`, { method: "PATCH", credentials: "include", headers: { "content-type": "application/json" }, body: JSON.stringify(patch) });
      if (!res.ok) { setEditError(`save failed: ${res.status} ${await res.text()}`); return; }
      setEditingId(null); refresh();
    } catch (e) { setEditError(`network error: ${(e as Error).message}`); }
    finally { setSaving(false); }
  };

  const del = async (id: number) => {
    const name = magis.find((m) => m.id === id)?.name ?? `#${id}`;
    if (!confirm(`${t("magis.deleteConfirm")} (${name})`)) return;
    const res = await fetch(`/api/magis/${id}`, { method: "DELETE", credentials: "include" });
    if (res.ok) refresh();
  };

  const setLifecycle = async (id: number, action: "start" | "stop") => {
    setLifecycleError(null); setLifecycleId(id);
    try {
      const res = await fetch(`/api/magis/${id}/runtime/${action}`, { method: "POST", credentials: "include" });
      if (!res.ok) { setLifecycleError(`${action} failed: ${res.status} ${await res.text()}`); return; }
      refresh();
    } catch (e) { setLifecycleError(`network error: ${(e as Error).message}`); }
    finally { setLifecycleId(null); }
  };

  return (
    <div className="space-y-4">
      <ConsoleCard
        title={t("magis.paneTitle")}
        headerRight={<InfoTip text={t("magis.paneDesc")} />}
        headerAction={
          <button type="button" className="btn btn-primary text-xs py-1.5 px-3"
            onClick={() => { setAddOpen((o) => !o); setAddError(null); }}>
            {addOpen ? t("common.cancel") : `+ ${t("magis.createHeading")}`}
          </button>
        }
      >
        {loadError && <p className="form-error mb-3">{loadError}</p>}
        {lifecycleError && <p className="form-error mb-3">{lifecycleError}</p>}

        {addOpen && (
          <div className="mb-5 rounded-lg border border-sky-light/40 bg-sky-pale/10 p-3">
            {addError && <p className="form-error mb-3">{addError}</p>}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 items-end">
              <label className="flex flex-col gap-1">
                <span className="form-label">{t("magis.createTeamLabel")}</span>
                <select className="form-input text-sm py-1.5 px-3 w-full" value={addForm.magic_id}
                  onChange={(e) => setAddForm((f) => ({ ...f, magic_id: e.target.value }))}>
                  <option value="" disabled>—</option>
                  {magics.map((c) => (<option key={c.id} value={String(c.id)}>{c.name}</option>))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="form-label">{t("magis.createNameLabel")}</span>
                <input className="form-input text-sm py-1.5 px-3 w-full" placeholder={t("magis.createNamePlaceholder")}
                  value={addForm.name} onChange={(e) => setAddForm((f) => ({ ...f, name: e.target.value }))} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="form-label">{t("magis.createPositionLabel")}</span>
                <select className="form-input text-sm py-1.5 px-3 w-full" value={addForm.magic_position}
                  onChange={(e) => setAddForm((f) => ({ ...f, magic_position: e.target.value as "adam" | "eve" }))}>
                  <option value="eve">EVE</option><option value="adam">ADAM</option>
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="form-label">{t("magis.createProviderLabel")}</span>
                <select className="form-input text-sm py-1.5 px-3 w-full" value={addForm.provider}
                  onChange={(e) => setAddForm((f) => ({ ...f, provider: e.target.value }))}>
                  {PROVIDER_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="form-label">{t("magis.createApiKeyLabel")}</span>
                <input type="password" className="form-input text-sm py-1.5 px-3 w-full"
                  value={addForm.api_key} onChange={(e) => setAddForm((f) => ({ ...f, api_key: e.target.value }))} />
              </label>
              <button type="button" disabled={adding || !addForm.magic_id}
                className="btn btn-primary text-sm py-1.5 px-4" onClick={() => { void submitCreate(); }}>
                {adding ? t("common.loading") : t("common.add")}
              </button>
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="data-table w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-3 font-medium">{t("magis.columnName")}</th>
                <th className="py-2 pr-3 font-medium">{t("magis.columnTeam")}</th>
                <th className="py-2 pr-3 font-medium">{t("magis.columnPosition")}</th>
                <th className="py-2 pr-3 font-medium">{t("magis.columnProvider")}</th>
                <th className="py-2 pr-3 font-medium">{t("magis.columnApiKey")}</th>
                <th className="py-2 pr-3 font-medium">{t("magis.columnRuntime")}</th>
                <th className="py-2 pr-3 font-medium w-24 text-right" />
              </tr>
            </thead>
            <tbody>
              {magis.map((m) => {
                const isEdit = editingId === m.id;
                const tname = teamName.get(m.magic_id);
                return (
                  <tr key={m.id} className={`border-b border-sky-light/30 transition-colors ${isEdit ? "bg-sky-pale/20" : "hover:bg-sky-pale/10"}`}>
                    {isEdit ? (
                      <td className="py-2 pr-3" colSpan={6}>
                        <div className="flex items-center gap-2">
                          <input className="form-input text-sm py-1 px-2 w-28" placeholder={t("magis.createNamePlaceholder")}
                            value={editDraft.name} onChange={(e) => setEditDraft((d) => ({ ...d, name: e.target.value }))} />
                          <select className="form-input text-sm py-1 px-2 w-40" value={editDraft.provider}
                            onChange={(e) => setEditDraft((d) => ({ ...d, provider: e.target.value }))}>
                            {PROVIDER_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
                          </select>
                          <input type="password" className="form-input text-sm py-1 px-2 w-40"
                            placeholder={t("magis.keyNewPlaceholder")} value={editDraft.api_key}
                            onChange={(e) => setEditDraft((d) => ({ ...d, api_key: e.target.value }))} />
                          <button type="button" disabled={saving} onClick={() => { void submitEdit(m.id); }}
                            className="btn btn-primary text-xs py-1 px-3">{saving ? "…" : t("common.save")}</button>
                          <button type="button" onClick={cancelEdit} className="btn btn-secondary text-xs py-1 px-2">{t("common.cancel")}</button>
                          {editError && <span className="text-xs text-rose-600">{editError}</span>}
                        </div>
                      </td>
                    ) : (
                      <>
                        <td className="py-2 pr-3">
                          <span className="font-medium text-ink text-sm">{m.name || <span className="text-ink-soft italic">#{m.id}</span>}</span>
                        </td>
                        <td className="py-2 pr-3">
                          {tname ? <span className="text-ink-soft text-xs">{tname}<span className="text-ink-soft/30 font-mono text-[11px] ml-1">#{m.magic_id}</span></span>
                            : <span className="text-ink-soft italic text-xs">#{m.magic_id}</span>}
                        </td>
                        <td className="py-2 pr-3">
                          <span className={`inline-flex text-[11px] font-medium rounded px-1.5 py-0.5 ${m.magic_position === "adam" ? "bg-amber-50 text-amber-700 border border-amber-200" : "bg-sky-50 text-sky-700 border border-sky-200"}`}>
                            {m.magic_position === "adam" ? t("magis.positionAdam") : t("magis.positionEve")}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-xs text-ink-soft">{m.provider ?? "—"}</td>
                        <td className="py-2 pr-3 font-mono text-[11px] text-ink-soft">{m.api_key_set ? `••••${m.api_key_last4 ?? ""}` : t("magis.keyNotSet")}</td>
                        <td className="py-2 pr-3 text-xs">
                          {m.magic_position === "eve" ? (
                            <span className={m.runtime?.observed_state === "failed" ? "text-rose-600" : "text-ink-soft"}>
                              {m.runtime?.observed_state ?? "draft"}
                            </span>
                          ) : "—"}
                        </td>
                      </>
                    )}
                    <td className="py-2 pr-3 text-right">
                      {isEdit ? null : (
                        <div className="flex items-center justify-end gap-0.5">
                          <button type="button" onClick={() => startEdit(m)}
                            title={t("common.edit")}
                            className="p-1 rounded text-ink-soft hover:text-ink hover:bg-white/60 transition-colors">
                            <IconEdit className="h-4 w-4" />
                          </button>
                          {m.magic_position === "eve" && (
                            <button type="button" disabled={lifecycleId === m.id}
                              onClick={() => { void setLifecycle(m.id, m.runtime?.desired_state === "running" ? "stop" : "start"); }}
                              title={m.runtime?.desired_state === "running" ? t("magis.stopEve") : t("magis.startEve")}
                              className="px-1.5 py-1 rounded text-[11px] text-sky-700 hover:bg-sky-pale transition-colors">
                              {lifecycleId === m.id ? "…" : m.runtime?.desired_state === "running" ? t("magis.stopEve") : t("magis.startEve")}
                            </button>
                          )}
                          <button type="button" onClick={() => { void del(m.id); }}
                            title={t("common.delete")}
                            className="p-1 rounded text-ink-soft hover:text-rose-600 hover:bg-white/60 transition-colors">
                            <IconDelete className="h-4 w-4" />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
              {magis.length === 0 && (
                <tr><td colSpan={7} className="py-6 text-ink-soft text-sm text-center">{t("magis.empty")}</td></tr>
              )}
            </tbody>
          </table>
        </div>

      </ConsoleCard>
    </div>
  );
}
