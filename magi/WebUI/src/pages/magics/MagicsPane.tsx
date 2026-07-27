/**
 * MagicsPane — MAGI Council management.
 */
import { Fragment, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import ConsoleCard from "../../components/ConsoleCard";
import { IconCheck, IconDelete, IconEdit, IconEye, IconX } from "../../components/icons";
import { InfoTip } from "../../components/InfoTip";
import { useT } from "../../i18n/index";
import { qk } from "../../lib/queryClient";
import { useMagics, useMagis, type MAGICRow, type MagiBrief } from "../../lib/queries";

// -- tree flatten ----------------------------------------------------------

type FlatMAGIC = MAGICRow & { depth: number };

function flattenTree(rows: MAGICRow[]): FlatMAGIC[] {
  const byId = new Map<number, MAGICRow & { children: MAGICRow[] }>();
  for (const r of rows) byId.set(r.id, { ...r, children: [] });
  const roots: MAGICRow[] = [];
  for (const r of rows) {
    const node = byId.get(r.id)!;
    if (r.parent_id != null && byId.has(r.parent_id)) byId.get(r.parent_id)!.children.push(node);
    else roots.push(node);
  }
  const sortByName = (xs: MAGICRow[]) => { xs.sort((a, b) => a.name.localeCompare(b.name)); xs.forEach((x) => sortByName(byId.get(x.id)!.children)); };
  sortByName(roots);
  const out: FlatMAGIC[] = [];
  (function walk(nodes: MAGICRow[], d: number) { for (const n of nodes) { out.push({ ...n, depth: d }); walk(byId.get(n.id)!.children, d + 1); } })(roots, 0);
  return out;
}

export function MagicsPane() {
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

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editParentId, setEditParentId] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [createParentId, setCreateParentId] = useState("");
  const [createName, setCreateName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  const tree = useMemo(() => flattenTree(magics), [magics]);
  const adamByMagic = useMemo(() => {
    const m = new Map<number, MagiBrief>(); for (const g of magis) { if (g.magic_position === "adam") m.set(g.magic_id, g); } return m;
  }, [magis]);
  // Children grouped by parent_id for the detail disclosure.
  const childrenByParent = useMemo(() => {
    const m = new Map<number, MAGICRow[]>();
    for (const r of magics) {
      if (r.parent_id != null) {
        const list = m.get(r.parent_id) ?? [];
        list.push(r);
        m.set(r.parent_id, list);
      }
    }
    for (const list of m.values()) list.sort((a, b) => a.name.localeCompare(b.name));
    return m;
  }, [magics]);
  const [detailId, setDetailId] = useState<number | null>(null);

  const startEdit = (r: MAGICRow) => { setEditingId(r.id); setEditName(r.name); setEditParentId(r.parent_id != null ? String(r.parent_id) : ""); setEditError(null); };
  const cancelEdit = () => { setEditingId(null); setEditError(null); };
  const submitEdit = async (id: number) => {
    setEditError(null);
    const body: Record<string, unknown> = { name: editName.trim() };
    if (editParentId) body.parent_id = Number.parseInt(editParentId, 10);
    else body.parent_id = null;
    setSaving(true);
    try {
      const res = await fetch(`/api/magics/${id}`, { method: "PATCH", credentials: "include", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (!res.ok) { setEditError(`save failed: ${res.status} ${await res.text()}`); return; }
      setEditingId(null); refresh();
    } catch (e) { setEditError(`network error: ${(e as Error).message}`); }
    finally { setSaving(false); }
  };

  const submitCreate = async () => {
    setCreateError(null);
    let pid: number | null = null;
    if (createParentId) { pid = Number.parseInt(createParentId, 10); if (!Number.isFinite(pid)) { setCreateError("invalid parent"); return; } }
    setCreating(true);
    try {
      const res = await fetch("/api/magics", { method: "POST", credentials: "include", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: createName.trim(), parent_id: pid }) });
      if (!res.ok) { setCreateError(`create failed: ${res.status} ${await res.text()}`); return; }
      setCreateParentId(""); setCreateName(""); setCreateOpen(false); refresh();
    } catch (e) { setCreateError(`network error: ${(e as Error).message}`); }
    finally { setCreating(false); }
  };

  const isParent = (id: number) => magics.some((r) => r.parent_id === id);
  const del = async (id: number, _name: string) => {
    if (isParent(id)) { alert("请先删除子团体"); return; }
    if (!confirm(t("magics.deleteConfirm"))) return;
    const res = await fetch(`/api/magics/${id}`, { method: "DELETE", credentials: "include" });
    if (res.ok) refresh(); else alert(`delete failed: ${res.status}`);
  };

  const parentOptions = [{ id: "", name: t("magics.createParentNone") }, ...magics];

  return (
    <div className="space-y-4">
      <ConsoleCard
        title={t("magics.paneTitle")}
        headerRight={<InfoTip text={t("magics.paneDesc")} />}
        headerAction={
          <button type="button" className="btn btn-primary text-xs py-1.5 px-3"
            onClick={() => { setCreateOpen((o) => !o); setCreateError(null); }}>
            {createOpen ? t("common.cancel") : `+ ${t("magics.createHeading")}`}
          </button>
        }
      >
        {loadError && <p className="form-error mb-3">{loadError}</p>}
        {magicsQuery.isLoading && <p className="text-sm text-ink-soft">{t("common.loading")}</p>}

        {createOpen && (
          <div className="mb-5 rounded-lg border border-sky-light/40 bg-sky-pale/10 p-3">
            {createError && <p className="form-error mb-2">{createError}</p>}
            <div className="flex items-center gap-2">
              <select className="form-input text-sm py-1.5 px-3" value={createParentId} onChange={(e) => setCreateParentId(e.target.value)}>
                {parentOptions.map((o) => (<option key={o.id} value={String(o.id)}>{o.name}</option>))}
              </select>
              <input className="form-input flex-1 text-sm py-1.5 px-3" placeholder={t("magics.createNamePlaceholder")}
                value={createName} onChange={(e) => setCreateName(e.target.value)} />
              <button type="button" disabled={creating || !createName.trim()} onClick={submitCreate}
                className="btn btn-primary text-sm py-1.5 px-4">{creating ? t("common.loading") : t("common.add")}</button>
            </div>
          </div>
        )}

        {!magicsQuery.isLoading && magics.length === 0 && (
          <p className="text-sm text-ink-soft">{t("magics.empty")}</p>
        )}
        {magics.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-3 font-medium w-2/5">{t("magics.columnName")}</th>
                <th className="py-2 pr-3 font-medium w-16">ID</th>
                <th className="py-2 pr-3 font-medium w-1/5">Adam</th>
                <th className="py-2 pr-3 font-medium w-20 text-right">成员</th>
                <th className="py-2 pr-3 font-medium w-20">{t("magics.columnActions")}</th>
              </tr>
            </thead>
            <tbody>
              {tree.map((r) => {
                const isEdit = editingId === r.id;
                const adam = adamByMagic.get(r.id);
                const prefix = r.depth > 0 ? "└ ".padStart(r.depth * 2 + 1, " ") : "";
                return (
                  <Fragment key={r.id}>
                    <tr className={`border-b border-sky-light/20 transition-colors ${isEdit ? "bg-sky-pale/20" : "hover:bg-sky-pale/10"}`}>
                    {isEdit ? (
                      <td className="py-2 pr-3" colSpan={5}>
                        <div className="flex items-center gap-2">
                          <input className="form-input text-sm py-1 px-2 w-40" value={editName} onChange={(e) => setEditName(e.target.value)} />
                          <select className="form-input text-sm py-1 px-2" value={editParentId} onChange={(e) => setEditParentId(e.target.value)}>
                            {parentOptions.map((o) => (<option key={o.id} value={String(o.id)}>{o.name}</option>))}
                          </select>
                          <button type="button" disabled={saving} onClick={() => { void submitEdit(r.id); }} title={t("common.save")}
                            className="p-1 rounded text-emerald-600 hover:text-emerald-800 hover:bg-white/60 transition-colors disabled:opacity-30">
                            {saving ? <span className="text-[10px]">…</span> : <IconCheck className="h-4 w-4" />}
                          </button>
                          <button type="button" onClick={cancelEdit} title={t("common.cancel")}
                            className="p-1 rounded text-ink-soft hover:text-ink hover:bg-white/60 transition-colors">
                            <IconX className="h-4 w-4" />
                          </button>
                          {editError && <span className="text-xs text-rose-600">{editError}</span>}
                        </div>
                      </td>
                    ) : (
                      <>
                        <td className="py-2.5 pr-3">
                          <span className="text-ocean/30 font-mono text-[11px] mr-1.5">{prefix}</span>
                          <span className="font-medium text-ink">{r.name}</span>
                        </td>
                        <td className="py-2.5 pr-3 font-mono text-[11px] text-ink-soft/40">#{r.id}</td>
                        <td className="py-2.5 pr-3">
                          {adam ? (
                            <span className="text-xs text-ink-soft">{adam.name || `#${adam.id}`}</span>
                          ) : (
                            <span className="text-xs text-ink-soft/30">—</span>
                          )}
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <span className="text-xs text-ink-soft">{r.member_count || "—"}</span>
                        </td>
                        <td className="py-2.5">
                          <div className="flex items-center gap-0.5 justify-end">
                            {r.child_count > 0 && (
                              <button type="button"
                                onClick={() => setDetailId(detailId === r.id ? null : r.id)}
                                title={t("magics.showChildren")}
                                className={`p-1 rounded transition-colors ${
                                  detailId === r.id
                                    ? "text-ocean bg-sky-pale/30"
                                    : "text-ink-soft hover:text-ink hover:bg-white/60"
                                }`}
                              >
                                <IconEye className="h-3.5 w-3.5" />
                              </button>
                            )}
                            <button type="button" onClick={() => startEdit(r)} title={t("common.edit")}
                              className="p-1 rounded text-ink-soft hover:text-ink hover:bg-white/60 transition-colors">
                              <IconEdit className="h-3.5 w-3.5" />
                            </button>
                            <button type="button" onClick={() => { void del(r.id, r.name); }} title={t("common.delete")}
                              className="p-1 rounded text-ink-soft hover:text-rose-600 hover:bg-white/60 transition-colors">
                              <IconDelete className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </td>
                      </>
                    )}
                  </tr>
                  {detailId === r.id && r.child_count > 0 && (
                    <tr key={`${r.id}-children`} className="border-b border-sky-light/20 bg-sky-pale/10">
                      <td colSpan={5} className="p-0">
                        <div className="px-4 py-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-soft">
                          <span className="text-ink-soft/60">{t("magics.columnChildren")}:</span>
                          {(childrenByParent.get(r.id) ?? []).map((ch) => (
                            <span key={ch.id} className="font-medium text-ink">{ch.name}</span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </ConsoleCard>
    </div>
  );
}
