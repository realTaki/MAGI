/**
 * MagisPane — MAGIS management.
 */
import { Fragment, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import ConsoleCard from "../../components/ConsoleCard";
import { IconCheck, IconDelete, IconEdit, IconEye, IconX } from "../../components/icons";
import { InfoTip } from "../../components/InfoTip";
import { useT } from "../../i18n/index";
import { qk } from "../../lib/queryClient";
import { useMagis, useMagic, type MagisRow, type MAGICRow } from "../../lib/queries";
import { SocietyControls } from "./SocietyControls";

// -- tree flatten ----------------------------------------------------------

type FlatMagis = MagisRow & { depth: number };

function flattenTree(rows: MagisRow[]): FlatMagis[] {
  const byId = new Map<number, MagisRow & { children: MagisRow[] }>();
  for (const r of rows) byId.set(r.id, { ...r, children: [] });
  const roots: MagisRow[] = [];
  for (const r of rows) {
    const node = byId.get(r.id)!;
    if (r.parent_id != null && byId.has(r.parent_id)) byId.get(r.parent_id)!.children.push(node);
    else roots.push(node);
  }
  const sortByName = (xs: MagisRow[]) => { xs.sort((a, b) => a.name.localeCompare(b.name)); xs.forEach((x) => sortByName(byId.get(x.id)!.children)); };
  sortByName(roots);
  const out: FlatMagis[] = [];
  (function walk(nodes: MagisRow[], d: number) { for (const n of nodes) { out.push({ ...n, depth: d }); walk(byId.get(n.id)!.children, d + 1); } })(roots, 0);
  return out;
}

export function MagisPane() {
  const t = useT();
  const qc = useQueryClient();
  const magisQuery = useMagis();
  const magicQuery = useMagic();
  const magis = magisQuery.data ?? [];
  const magic = magicQuery.data ?? [];
  const loadError = (magisQuery.error || magicQuery.error)
    ? (magisQuery.error instanceof Error ? magisQuery.error.message : "") || (magicQuery.error instanceof Error ? magicQuery.error.message : "") || "load failed"
    : null;

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: qk.magis });
    void qc.invalidateQueries({ queryKey: qk.magic });
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

  const tree = useMemo(() => flattenTree(magis), [magis]);
  const adamByMagis = useMemo(() => {
    const m = new Map<number, MAGICRow>();
    for (const g of magic) for (const membership of g.memberships) {
      if (membership.role_name === "ADAM") m.set(membership.magis_id, g);
    }
    return m;
  }, [magic]);
  const childrenByParent = useMemo(() => {
    const m = new Map<number, MagisRow[]>();
    for (const r of magis) {
      if (r.parent_id != null) {
        const list = m.get(r.parent_id) ?? [];
        list.push(r);
        m.set(r.parent_id, list);
      }
    }
    for (const list of m.values()) list.sort((a, b) => a.name.localeCompare(b.name));
    return m;
  }, [magis]);
  const [detailId, setDetailId] = useState<number | null>(null);

  const startEdit = (r: MagisRow) => { setEditingId(r.id); setEditName(r.name); setEditParentId(r.parent_id != null ? String(r.parent_id) : ""); setEditError(null); };
  const cancelEdit = () => { setEditingId(null); setEditError(null); };
  const submitEdit = async (id: number) => {
    setEditError(null);
    const body: Record<string, unknown> = { name: editName.trim() };
    if (editParentId) body.parent_id = Number.parseInt(editParentId, 10);
    else body.parent_id = null;
    setSaving(true);
    try {
      const res = await fetch(`/api/magis/${id}`, { method: "PATCH", credentials: "include", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
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
      const res = await fetch("/api/magis", { method: "POST", credentials: "include", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: createName.trim(), parent_id: pid }) });
      if (!res.ok) { setCreateError(`create failed: ${res.status} ${await res.text()}`); return; }
      setCreateParentId(""); setCreateName(""); setCreateOpen(false); refresh();
    } catch (e) { setCreateError(`network error: ${(e as Error).message}`); }
    finally { setCreating(false); }
  };

  const isParent = (id: number) => magis.some((r) => r.parent_id === id);
  const del = async (id: number, _name: string) => {
    if (isParent(id)) { alert("请先删除子团体"); return; }
    if (!confirm(t("magis.deleteConfirm"))) return;
    const res = await fetch(`/api/magis/${id}`, { method: "DELETE", credentials: "include" });
    if (res.ok) refresh(); else alert(`delete failed: ${res.status}`);
  };

  const parentOptions = [{ id: "", name: t("magis.createParentNone") }, ...magis];

  return (
    <div className="space-y-4">
      <ConsoleCard
        title={t("magis.paneTitle")}
        headerRight={<InfoTip text={t("magis.paneDesc")} />}
        headerAction={
          <button type="button" className="btn btn-primary text-xs py-1.5 px-3"
            onClick={() => { setCreateOpen((o) => !o); setCreateError(null); }}>
            {createOpen ? t("common.cancel") : `+ ${t("magis.createHeading")}`}
          </button>
        }
      >
        {loadError && <p className="form-error mb-3">{loadError}</p>}
        {magisQuery.isLoading && <p className="text-sm text-ink-3">{t("common.loading")}</p>}

        {createOpen && (
          <div className="mb-5 rounded-lg border border-border bg-sky-soft p-3">
            {createError && <p className="form-error mb-2">{createError}</p>}
            <div className="flex items-center gap-2">
              <select className="form-input text-sm py-1.5 px-3" value={createParentId} onChange={(e) => setCreateParentId(e.target.value)}>
                {parentOptions.map((o) => (<option key={o.id} value={String(o.id)}>{o.name}</option>))}
              </select>
              <input className="form-input flex-1 text-sm py-1.5 px-3" placeholder={t("magis.createNamePlaceholder")}
                value={createName} onChange={(e) => setCreateName(e.target.value)} />
              <button type="button" disabled={creating || !createName.trim()} onClick={submitCreate}
                className="btn btn-primary text-sm py-1.5 px-4">{creating ? t("common.loading") : t("common.add")}</button>
            </div>
          </div>
        )}

        {!magisQuery.isLoading && magis.length === 0 && (
          <p className="text-sm text-ink-3">{t("magis.empty")}</p>
        )}
        {magis.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-3 border-b border-border">
                <th className="py-2 pr-3 font-medium w-2/5">{t("magis.columnName")}</th>
                <th className="py-2 pr-3 font-medium w-16">ID</th>
                <th className="py-2 pr-3 font-medium w-1/5">Adam</th>
                <th className="py-2 pr-3 font-medium w-20 text-right">成员</th>
                <th className="py-2 pr-3 font-medium w-20">{t("magis.columnActions")}</th>
              </tr>
            </thead>
            <tbody>
              {tree.map((r) => {
                const isEdit = editingId === r.id;
                const adam = adamByMagis.get(r.id);
                const prefix = r.depth > 0 ? "└ ".padStart(r.depth * 2 + 1, " ") : "";
                return (
                  <Fragment key={r.id}>
                    <tr className={`border-b border-border-2 transition-colors ${isEdit ? "bg-accent-soft" : "hover:bg-surface-2"}`}>
                    {isEdit ? (
                      <td className="py-2 pr-3" colSpan={5}>
                        <div className="flex items-center gap-2">
                          <input className="form-input text-sm py-1 px-2 w-40" value={editName} onChange={(e) => setEditName(e.target.value)} />
                          <select className="form-input text-sm py-1 px-2" value={editParentId} onChange={(e) => setEditParentId(e.target.value)}>
                            {parentOptions.map((o) => (<option key={o.id} value={String(o.id)}>{o.name}</option>))}
                          </select>
                          <button type="button" disabled={saving} onClick={() => { void submitEdit(r.id); }} title={t("common.save")}
                            className="p-1 rounded text-success hover:text-success hover:bg-surface-2 transition-colors disabled:opacity-30">
                            {saving ? <span className="text-[10px]">…</span> : <IconCheck className="h-4 w-4" />}
                          </button>
                          <button type="button" onClick={cancelEdit} title={t("common.cancel")}
                            className="p-1 rounded text-ink-3 hover:text-ink hover:bg-surface-2 transition-colors">
                            <IconX className="h-4 w-4" />
                          </button>
                          {editError && <span className="text-xs text-danger">{editError}</span>}
                        </div>
                      </td>
                    ) : (
                      <>
                        <td className="py-2.5 pr-3">
                          <span className="text-sky-ink/30 font-mono text-[11px] mr-1.5">{prefix}</span>
                          <span className="font-medium text-ink">{r.name}</span>
                        </td>
                        <td className="py-2.5 pr-3 font-mono text-[11px] text-ink-3/40">#{r.id}</td>
                        <td className="py-2.5 pr-3">
                          {adam ? (
                            <span className="text-xs text-ink-3">{adam.name || `#${adam.id}`}</span>
                          ) : (
                            <span className="text-xs text-ink-3/30">—</span>
                          )}
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <span className="text-xs text-ink-3">{r.member_count || "—"}</span>
                        </td>
                        <td className="py-2.5">
                          <div className="flex items-center gap-0.5 justify-end">
                            {r.child_count > 0 && (
                              <button type="button"
                                onClick={() => setDetailId(detailId === r.id ? null : r.id)}
                                title={t("magis.showChildren")}
                                className={`p-1 rounded transition-colors ${
                                  detailId === r.id
                                    ? "text-accent bg-accent-soft"
                                    : "text-ink-3 hover:text-ink hover:bg-surface-2"
                                }`}
                              >
                                <IconEye className="h-3.5 w-3.5" />
                              </button>
                            )}
                            <button type="button" onClick={() => setDetailId(detailId === r.id ? null : r.id)} className="text-xs text-accent px-1">Manage</button>
                            <button type="button" onClick={() => startEdit(r)} title={t("common.edit")}
                              className="p-1 rounded text-ink-3 hover:text-ink hover:bg-surface-2 transition-colors">
                              <IconEdit className="h-3.5 w-3.5" />
                            </button>
                            <button type="button" onClick={() => { void del(r.id, r.name); }} title={t("common.delete")}
                              className="p-1 rounded text-ink-3 hover:text-danger hover:bg-surface-2 transition-colors">
                              <IconDelete className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </td>
                      </>
                    )}
                  </tr>
                  {detailId === r.id && r.child_count > 0 && (
                    <tr key={`${r.id}-children`} className="border-b border-border-2 bg-surface-2">
                      <td colSpan={5} className="p-0">
                        <div className="px-4 py-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-3">
                          <span className="text-ink-3/60">{t("magis.columnChildren")}:</span>
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
      {detailId !== null && magis.find((m) => m.id === detailId) && (
        <SocietyControls society={magis.find((m) => m.id === detailId)!} onChanged={refresh} />
      )}
    </div>
  );
}
