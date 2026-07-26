/**
 * MagicsPane — MAGI Council management.
 */
import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import ConsoleCard from "../../components/ConsoleCard";
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

const INDENT = 24;

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
        headerRight={
          <div className="flex items-center gap-2">
            <InfoTip text={t("magics.paneDesc")} />
            <button type="button" className="btn btn-primary text-xs py-1.5 px-3"
              onClick={() => { setCreateOpen((o) => !o); setCreateError(null); }}>
              {createOpen ? t("common.cancel") : `+ ${t("magics.createHeading")}`}
            </button>
          </div>
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
          <div className="space-y-0.5">
            {tree.map((r) => {
              const isEdit = editingId === r.id;
              const adam = adamByMagic.get(r.id);
              const indent = r.depth * INDENT;
              const prefix = r.depth === 0 ? "" : "├─".padStart(r.depth * 2, " ");
              return (
                <div key={r.id}
                  className={`flex items-center gap-2 px-2 py-1 rounded transition-colors ${isEdit ? "bg-sky-pale/20" : "hover:bg-sky-pale/10"}`}
                  style={{ paddingLeft: 8 + indent }}
                >
                  {isEdit ? (
                    <div className="flex items-center gap-2 flex-1">
                      <input className="form-input text-sm py-1 px-2 w-40" value={editName} onChange={(e) => setEditName(e.target.value)} />
                      <select className="form-input text-sm py-1 px-2" value={editParentId} onChange={(e) => setEditParentId(e.target.value)}>
                        {parentOptions.map((o) => (<option key={o.id} value={String(o.id)}>{o.name}</option>))}
                      </select>
                      <button type="button" disabled={saving} onClick={() => { void submitEdit(r.id); }}
                        className="btn btn-primary text-xs py-0.5 px-2">{saving ? "…" : t("common.save")}</button>
                      <button type="button" onClick={cancelEdit} className="btn btn-secondary text-xs py-0.5 px-1.5">{t("common.cancel")}</button>
                      {editError && <span className="text-xs text-rose-600">{editError}</span>}
                    </div>
                  ) : (
                    <>
                      <span className="text-ocean/30 font-mono text-[11px] shrink-0">{prefix}</span>
                      <span className="text-sm font-medium text-ink truncate">{r.name}</span>
                      <span className="font-mono text-[11px] text-ink-soft/40">#{r.id}</span>
                      {adam && <span className="status-pill status-pill--connected text-[10px]">ADAM</span>}
                      {r.child_count > 0 && <span className="text-[11px] text-ink-soft">{r.child_count} sub</span>}
                      <div className="flex items-center gap-1 ml-auto">
                        <button type="button" onClick={() => startEdit(r)} className="btn btn-secondary text-xs py-0.5 px-1.5">{t("common.edit")}</button>
                        <button type="button" onClick={() => { void del(r.id, r.name); }}
                          className="btn btn-secondary text-xs py-0.5 px-1.5 text-rose-600 hover:text-rose-800">{t("common.delete")}</button>
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </ConsoleCard>
    </div>
  );
}
