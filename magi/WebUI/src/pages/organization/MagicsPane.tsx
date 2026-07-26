/**
 * MagicsPane — MAGI Council management.
 *
 * Tree of councils, each with a leader (ADAM) and members (EVEs).
 * Full CRUD with inline editing.
 */
import { useEffect, useMemo, useState } from "react";

import ConsoleCard from "../../components/ConsoleCard";
import { InfoTip } from "../../components/InfoTip";
import { useT } from "../../i18n/index";

// -- types --------------------------------------------------------------------

export type MAGICRow = {
  id: number; name: string; parent_id: number | null;
  adam_id: number | null; child_count: number;
  created_at: string; updated_at: string;
};

export type MagiBrief = {
  id: number; magic_id: number; magic_position: string;
  provider: string | null; api_key_set: boolean; api_key_last4: string | null;
  created_at: string; updated_at: string;
};

// -- tree flatten -------------------------------------------------------------

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

// -- pane ---------------------------------------------------------------------

const INDENT = 24;

export function MagicsPane() {
  const t = useT();
  const [magics, setMagics] = useState<MAGICRow[] | null>(null);
  const [magis, setMagis] = useState<MagiBrief[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const reload = async () => {
    setLoadError(null);
    try {
      const [mr, gr] = await Promise.all([
        fetch("/api/magics", { credentials: "include" }),
        fetch("/api/magis", { credentials: "include" }),
      ]);
      if (!mr.ok || !gr.ok) { setLoadError(`load failed (magics=${mr.status}, magis=${gr.status})`); return; }
      setMagics((await mr.json()) as MAGICRow[]);
      setMagis((await gr.json()) as MagiBrief[]);
    } catch (e) { setLoadError(`network error: ${(e as Error).message}`); }
  };
  useEffect(() => { void reload(); }, []);

  const [createName, setCreateName] = useState("");
  const [createParent, setCreateParent] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState({ name: "", parent_id: "", adam_id: "" });
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());



  const flat = useMemo(() => (magics ? flattenTree(magics) : []), [magics]);
  const adams = useMemo(() => (magis ?? []).filter((m) => m.magic_position === "adam"), [magis]);

  const visibleIds = useMemo(() => {
    const vis = new Set<number>();
    const byId = new Map<number, MAGICRow & { children: MAGICRow[] }>();
    for (const r of magics ?? []) byId.set(r.id, { ...r, children: [] });
    for (const r of magics ?? []) { const n = byId.get(r.id)!; if (r.parent_id != null && byId.has(r.parent_id)) byId.get(r.parent_id)!.children.push(n); }
    const roots = (magics ?? []).filter((r) => r.parent_id == null || !byId.has(r.parent_id));
    const stack: { id: number; children: MAGICRow[] }[] = roots.map((r) => ({ id: r.id, children: byId.get(r.id)!.children }));
    while (stack.length) {
      const { id, children } = stack.pop()!;
      vis.add(id);
      if (expanded.has(id)) for (const ch of [...children].reverse()) stack.push({ id: ch.id, children: byId.get(ch.id)!.children });
    }
    return vis;
  }, [magics, expanded]);

  const visibleFlat = useMemo(() => flat.filter((f) => visibleIds.has(f.id)), [flat, visibleIds]);
  const toggle = (id: number) => setExpanded((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const submitCreate = async () => {
    setCreateError(null);
    if (!createName.trim()) { setCreateError(t("magics.nameDuplicateError")); return; }
    setCreating(true);
    try {
      const body: Record<string, unknown> = { name: createName.trim() };
      const pid = Number.parseInt(createParent, 10);
      if (Number.isFinite(pid) && pid > 0) body.parent_id = pid;
      const res = await fetch("/api/magics", { method: "POST", credentials: "include", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (!res.ok) { setCreateError(`create failed: ${res.status} ${await res.text()}`); return; }
      setCreateName(""); setCreateParent(""); setCreateOpen(false); await reload();
    } catch (e) { setCreateError(`network error: ${(e as Error).message}`); }
    finally { setCreating(false); }
  };

  const startEdit = (row: MAGICRow) => {
    setEditingId(row.id);
    setEditDraft({ name: row.name, parent_id: row.parent_id != null ? String(row.parent_id) : "", adam_id: row.adam_id != null ? String(row.adam_id) : "" });
    setEditError(null);
  };
  const cancelEdit = () => { setEditingId(null); setEditError(null); };
  const submitEdit = async (id: number) => {
    setEditError(null);
    const patch: Record<string, unknown> = {};
    const nn = editDraft.name.trim(); if (nn) patch.name = nn;
    const pid = Number.parseInt(editDraft.parent_id, 10);
    patch.parent_id = Number.isFinite(pid) && pid > 0 ? pid : editDraft.parent_id === "" ? null : undefined;
    const aid = Number.parseInt(editDraft.adam_id, 10);
    patch.adam_id = Number.isFinite(aid) && aid > 0 ? aid : editDraft.adam_id === "" ? null : undefined;
    setSaving(true);
    try {
      const res = await fetch(`/api/magics/${id}`, { method: "PATCH", credentials: "include", headers: { "content-type": "application/json" }, body: JSON.stringify(patch) });
      if (!res.ok) { setEditError(`save failed: ${res.status} ${await res.text()}`); return; }
      setEditingId(null); await reload();
    } catch (e) { setEditError(`network error: ${(e as Error).message}`); }
    finally { setSaving(false); }
  };

  const del = async (id: number, name: string) => {
    if (!confirm(t("magics.deleteConfirm"))) return;
    const res = await fetch(`/api/magics/${id}`, { method: "DELETE", credentials: "include" });
    if (res.ok) await reload(); else alert(`delete failed: ${res.status}`);
  };

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

        {createOpen && (
          <div className="mb-5 rounded-lg border border-sky-light/40 bg-sky-pale/10 p-3">
            {createError && <p className="form-error mb-3">{createError}</p>}
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1">
                <span className="form-label">{t("magics.createNameLabel")}</span>
                <input className="form-input text-sm py-1.5 px-3 w-36" placeholder={t("magics.createNamePlaceholder")}
                  value={createName} onChange={(e) => setCreateName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") void submitCreate(); }} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="form-label">{t("magics.createParentLabel")}</span>
                <select className="form-input text-sm py-1.5 px-3" value={createParent} onChange={(e) => setCreateParent(e.target.value)}>
                  <option value="">{t("magics.createParentNone")}</option>
                  {(magics ?? []).map((m) => (<option key={m.id} value={String(m.id)}>{m.name}</option>))}
                </select>
              </label>
              <button type="button" disabled={creating || !createName.trim()}
                className="btn btn-primary text-sm py-1.5 px-4" onClick={() => void submitCreate()}>
                {creating ? t("common.loading") : t("common.add")}
              </button>
            </div>
          </div>
        )}

        {/* Tree */}
        <div className="overflow-x-auto">
          {visibleFlat.length === 0 && !loadError && (
            <p className="py-6 text-ink-soft text-sm text-center">{t("magics.empty")}</p>
          )}
          {visibleFlat.map((m) => {
            const isEdit = editingId === m.id;
            const hasChildren = m.child_count > 0;
            const open = hasChildren && expanded.has(m.id);
            return (
              <div key={m.id} className={`border-b border-sky-light/20 ${isEdit ? "bg-sky-pale/20" : "hover:bg-sky-pale/10"} transition-colors`}>
                {isEdit ? (
                  <div className="flex items-center gap-2 px-2 py-2">
                    <input className="form-input text-sm py-1 px-2 w-32" value={editDraft.name}
                      onChange={(e) => setEditDraft((d) => ({ ...d, name: e.target.value }))} />
                    <select className="form-input text-sm py-1 px-2" value={editDraft.parent_id}
                      onChange={(e) => setEditDraft((d) => ({ ...d, parent_id: e.target.value }))}>
                      <option value="">{t("magics.createParentNone")}</option>
                      {magics?.filter((x) => x.id !== m.id).map((x) => (<option key={x.id} value={String(x.id)}>{x.name}</option>))}
                    </select>
                    <select className="form-input text-sm py-1 px-2" value={editDraft.adam_id}
                      onChange={(e) => setEditDraft((d) => ({ ...d, adam_id: e.target.value }))}>
                      <option value="">{t("magics.editAdamNone")}</option>
                      {adams.map((a) => (<option key={a.id} value={String(a.id)}>#{a.id} ({a.provider ?? "?"})</option>))}
                    </select>
                    <button type="button" disabled={saving} onClick={() => void submitEdit(m.id)}
                      className="btn btn-primary text-xs py-1 px-3">{saving ? "…" : t("common.save")}</button>
                    <button type="button" onClick={cancelEdit} className="btn btn-secondary text-xs py-1 px-2">{t("common.cancel")}</button>
                    {editError && <span className="text-xs text-rose-600">{editError}</span>}
                  </div>
                ) : (
                  <div className="flex items-center gap-3 px-2 py-2.5" style={{ paddingLeft: `${8 + m.depth * INDENT}px` }}>
                    <button type="button" disabled={!hasChildren} onClick={() => toggle(m.id)}
                      className={`w-5 h-5 flex items-center justify-center rounded text-xs ${hasChildren ? "text-ink-soft hover:bg-sky-light/60" : "text-ink-soft/15"}`}>
                      {hasChildren ? (open ? "▾" : "▸") : ""}
                    </button>
                    <span className="font-medium text-ink text-sm flex-1">
                      {m.depth > 0 && <span className="text-ink-soft/30 mr-1.5 select-none">{open ? "└┬" : "├─"}</span>}
                      {m.name}
                      <span className="text-ink-soft/30 font-mono text-[11px] ml-2">#{m.id}</span>
                    </span>
                    {m.adam_id != null && (
                      <span className="status-pill status-pill--connected text-[11px] shrink-0">ADAM #{m.adam_id}</span>
                    )}
                    <span className="text-xs text-ink-soft/50 font-mono shrink-0 w-8 text-center">{m.child_count > 0 ? m.child_count : ""}</span>
                    <div className="flex items-center gap-1 shrink-0">
                      <button type="button" onClick={() => startEdit(m)} className="btn btn-secondary text-xs py-0.5 px-1.5">{t("common.edit")}</button>
                      <button type="button" onClick={() => void del(m.id, m.name)}
                        className="btn btn-secondary text-xs py-0.5 px-1.5 text-rose-600 hover:text-rose-800">{t("common.delete")}</button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </ConsoleCard>
    </div>
  );
}



