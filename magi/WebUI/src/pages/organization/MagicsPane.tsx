/**
 * MagicsPane — "MAGI 团队 / MAGIC (MAGI Council)" management pane.
 *
 * Full CRUD for the MAGIC team tree (replaces the old
 * employee management" section). Features:
 *   - Tree view with expand/collapse (DFS flatten, depth-indented rows).
 *   - Create new MAGIC team (name + optional parent).
 *   - Inline edit: rename, reparent, assign ADAM Magi.
 *   - Delete with cascade warning.
 *
 * Reads/writes ``GET/POST/PATCH/DELETE /api/magics`` + reads
 * ``GET /api/magis`` (for the adam picker dropdown).
 */

import { useEffect, useMemo, useState } from "react";

import ConsoleCard from "../../components/ConsoleCard";
import { useT } from "../../i18n/index";

// -- backend wire shapes ----------------------------------------------------

export type MAGICRow = {
  id: number;
  name: string;
  parent_id: number | null;
  adam_id: number | null;
  child_count: number;
  created_at: string;
  updated_at: string;
};

export type MagiBrief = {
  id: number;
  magic_id: number;
  magic_position: string;
  provider: string | null;
  api_key_set: boolean;
  api_key_last4: string | null;
  created_at: string;
  updated_at: string;
};

// -- tree flatten -----------------------------------------------------------

type FlatMAGIC = MAGICRow & { depth: number };

function flattenTree(rows: MAGICRow[]): FlatMAGIC[] {
  const byId = new Map<number, MAGICRow & { children: MAGICRow[] }>();
  for (const r of rows) {
    byId.set(r.id, { ...r, children: [] });
  }
  const roots: MAGICRow[] = [];
  for (const r of rows) {
    const node = byId.get(r.id)!;
    if (r.parent_id != null && byId.has(r.parent_id)) {
      byId.get(r.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const sortByName = (xs: MAGICRow[]) => {
    xs.sort((a, b) => a.name.localeCompare(b.name));
    xs.forEach((x) => sortByName(byId.get(x.id)!.children));
  };
  sortByName(roots);

  const out: FlatMAGIC[] = [];
  const walk = (nodes: MAGICRow[], depth: number) => {
    for (const n of nodes) {
      out.push({ ...n, depth });
      walk(byId.get(n.id)!.children, depth + 1);
    }
  };
  walk(roots, 0);
  return out;
}

// -- helpers ----------------------------------------------------------------

/** Valid adam candidates: magis with magic_position='adam'. */
function adamCandidates(magis: MagiBrief[]): MagiBrief[] {
  return magis.filter((m) => m.magic_position === "adam");
}

// -- pane -------------------------------------------------------------------

export function MagicsPane() {
  const t = useT();
  const [magics, setMagics] = useState<MAGICRow[] | null>(null);
  const [magis, setMagis] = useState<MagiBrief[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Create-form state.
  const [createName, setCreateName] = useState("");
  const [createParent, setCreateParent] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Edit state — which row is being edited + its draft values.
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<{
    name: string;
    parent_id: string;
    adam_id: string;
  }>({ name: "", parent_id: "", adam_id: "" });
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Expand state: set of ids whose children are visible.
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

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
      setMagis((await gr.json()) as MagiBrief[]);
    } catch (e) {
      setLoadError(`network error: ${(e as Error).message}`);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const flat = useMemo(() => (magics ? flattenTree(magics) : []), [magics]);
  const adams = useMemo(
    () => (magis ? adamCandidates(magis) : []),
    [magis],
  );

  // Only render children of expanded nodes.
  const visibleIds = useMemo(() => {
    const vis = new Set<number>();
    const stack: { id: number; depth: number; children: MAGICRow[] }[] = [];

    // Build lookup.
    const byId = new Map<number, MAGICRow & { children: MAGICRow[] }>();
    for (const r of magics ?? []) {
      byId.set(r.id, { ...r, children: [] });
    }
    for (const r of magics ?? []) {
      const node = byId.get(r.id)!;
      if (r.parent_id != null && byId.has(r.parent_id)) {
        byId.get(r.parent_id)!.children.push(node);
      }
    }

    // Walk.
    const roots = (magics ?? []).filter((r) => r.parent_id == null || !byId.has(r.parent_id));
    for (const r of roots) {
      stack.push({ id: r.id, depth: 0, children: byId.get(r.id)!.children });
    }

    while (stack.length > 0) {
      const { id, depth, children } = stack.pop()!;
      vis.add(id);
      if (expanded.has(id)) {
        for (const ch of [...children].reverse()) {
          stack.push({
            id: ch.id,
            depth: depth + 1,
            children: byId.get(ch.id)!.children,
          });
        }
      }
    }
    return vis;
  }, [magics, expanded]);

  // Pre-sorted flat list filtered to visible nodes.
  const visibleFlat = useMemo(
    () => flat.filter((f) => visibleIds.has(f.id)),
    [flat, visibleIds],
  );

  const isExpanded = (id: number) => expanded.has(id);
  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // -- create ---------------------------------------------------------------
  const submitCreate = async () => {
    setCreateError(null);
    const name = createName.trim();
    if (!name) {
      setCreateError(t("magics.nameDuplicateError"));
      return;
    }
    setCreating(true);
    try {
      const body: Record<string, unknown> = { name };
      const pid = Number.parseInt(createParent, 10);
      if (Number.isFinite(pid) && pid > 0) body.parent_id = pid;

      const res = await fetch("/api/magics", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const b = await res.text();
        setCreateError(`create failed: ${res.status} ${b}`);
        return;
      }
      setCreateName("");
      setCreateParent("");
      await reload();
    } catch (e) {
      setCreateError(`network error: ${(e as Error).message}`);
    } finally {
      setCreating(false);
    }
  };

  // -- edit -----------------------------------------------------------------
  const startEdit = (row: MAGICRow) => {
    setEditingId(row.id);
    setEditDraft({
      name: row.name,
      parent_id: row.parent_id != null ? String(row.parent_id) : "",
      adam_id: row.adam_id != null ? String(row.adam_id) : "",
    });
    setEditError(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditError(null);
  };

  const submitEdit = async (id: number) => {
    setEditError(null);
    const patch: Record<string, unknown> = {};
    const newName = editDraft.name.trim();
    if (newName) patch.name = newName;
    const pid = Number.parseInt(editDraft.parent_id, 10);
    if (Number.isFinite(pid) && pid > 0) {
      patch.parent_id = pid;
    } else if (editDraft.parent_id === "") {
      patch.parent_id = null;
    }
    const aid = Number.parseInt(editDraft.adam_id, 10);
    if (Number.isFinite(aid) && aid > 0) {
      patch.adam_id = aid;
    } else if (editDraft.adam_id === "") {
      patch.adam_id = null;
    }

    setSaving(true);
    try {
      const res = await fetch(`/api/magics/${id}`, {
        method: "PATCH",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const b = await res.text();
        setEditError(`save failed: ${res.status} ${b}`);
        return;
      }
      setEditingId(null);
      await reload();
    } catch (e) {
      setEditError(`network error: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  // -- delete ---------------------------------------------------------------
  const deleteMagic = async (id: number, name: string) => {
    if (!confirm(t("magics.deleteConfirm") || `Delete team "${name}"? This will cascade-delete its Magis.`)) return;
    const res = await fetch(`/api/magics/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (res.ok) await reload();
    else {
      const b = await res.text();
      alert(`delete failed: ${res.status} ${b}`);
    }
  };

  // -- render ---------------------------------------------------------------
  const rows = visibleFlat;

  return (
    <div className="space-y-4">
      <ConsoleCard title={t("magics.paneTitle")}>
        {loadError !== null && (
          <p className="text-sm text-red-600 mb-3">{loadError}</p>
        )}

        {/* Tree table */}
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-ink-soft border-b border-ink-soft/20">
                <th className="py-2 pr-3 w-8"></th>
                <th className="py-2 pr-3">{t("magics.columnName")}</th>
                <th className="py-2 pr-3">{t("magics.columnParent")}</th>
                <th className="py-2 pr-3">{t("magics.columnAdam")}</th>
                <th className="py-2 pr-3">{t("magics.columnChildren")}</th>
                <th className="py-2 pr-3">{t("magics.columnActions")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => {
                const isEdit = editingId === m.id;
                const hasChildren = m.child_count > 0;
                const expandedRow = isExpanded(m.id);
                return (
                  <tr key={m.id} className="border-b border-ink-soft/10">
                    {/* Expand toggle */}
                    <td className="py-1.5 pr-3">
                      <button
                        type="button"
                        disabled={!hasChildren}
                        onClick={() => toggleExpand(m.id)}
                        className={`text-xs w-5 h-5 inline-flex items-center justify-center rounded
                          ${hasChildren ? "hover:bg-sky-light/60 text-ink" : "text-ink-soft/30 cursor-default"}`}
                        aria-label={expandedRow ? t("sidebar.orgCollapseChildren") : t("sidebar.orgExpandChildren")}
                      >
                        {hasChildren ? (expandedRow ? "▾" : "▸") : " "}
                      </button>
                    </td>
                    {/* Name */}
                    {isEdit ? (
                      <td className="py-1.5 pr-3" colSpan={4} style={{ paddingLeft: `${0.5 + m.depth * 1.5}rem` }}>
                        <div className="flex flex-wrap items-center gap-2">
                          <input
                            className="rounded border border-ink-soft/30 px-2 py-0.5 text-sm w-40"
                            value={editDraft.name}
                            onChange={(e) => setEditDraft((d) => ({ ...d, name: e.target.value }))}
                          />
                          <select
                            className="rounded border border-ink-soft/30 px-2 py-0.5 text-sm"
                            value={editDraft.parent_id}
                            onChange={(e) => setEditDraft((d) => ({ ...d, parent_id: e.target.value }))}
                          >
                            <option value="">{t("magics.createParentNone")}</option>
                            {magics?.filter((x) => x.id !== m.id).map((x) => (
                              <option key={x.id} value={String(x.id)}>{x.name}</option>
                            ))}
                          </select>
                          <select
                            className="rounded border border-ink-soft/30 px-2 py-0.5 text-sm"
                            value={editDraft.adam_id}
                            onChange={(e) => setEditDraft((d) => ({ ...d, adam_id: e.target.value }))}
                          >
                            <option value="">{t("magics.editAdamNone")}</option>
                            {adams.map((a) => (
                              <option key={a.id} value={String(a.id)}>
                                #{a.id} ({a.provider ?? "?"})
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            disabled={saving}
                            className="rounded bg-ink text-paper px-2 py-0.5 text-xs disabled:opacity-50"
                            onClick={() => void submitEdit(m.id)}
                          >
                            {saving ? "…" : "save"}
                          </button>
                          <button
                            type="button"
                            className="text-xs text-ink-soft hover:underline"
                            onClick={cancelEdit}
                          >
                            cancel
                          </button>
                          {editError && (
                            <span className="text-xs text-red-600">{editError}</span>
                          )}
                        </div>
                      </td>
                    ) : (
                      <td className="py-1.5 pr-3" style={{ paddingLeft: `${0.5 + m.depth * 1.5}rem` }}>
                        {m.depth > 0 && <span className="text-ink-soft mr-1">└─</span>}
                        <span className="font-medium">{m.name}</span>
                        <span className="text-ink-soft font-mono text-xs ml-2">#{m.id}</span>
                      </td>
                    )}
                    {/* Non-edit columns (hidden during edit) */}
                    {!isEdit && (
                      <>
                        <td className="py-1.5 pr-3 font-mono text-ink-soft text-xs">
                          {m.parent_id != null
                            ? magics?.find((x) => x.id === m.parent_id)?.name ?? `#${m.parent_id}`
                            : "—"}
                        </td>
                        <td className="py-1.5 pr-3 text-xs">
                          {m.adam_id != null ? (
                            <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 font-mono">
                              #{m.adam_id}
                            </span>
                          ) : (
                            <span className="text-ink-soft">—</span>
                          )}
                        </td>
                        <td className="py-1.5 pr-3 font-mono text-ink-soft text-center">
                          {m.child_count}
                        </td>
                        <td className="py-1.5 pr-3">
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              className="text-xs text-sky-700 hover:underline"
                              onClick={() => startEdit(m)}
                            >
                              edit
                            </button>
                            <button
                              type="button"
                              className="text-xs text-red-600 hover:underline"
                              onClick={() => void deleteMagic(m.id, m.name)}
                            >
                              delete
                            </button>
                          </div>
                        </td>
                      </>
                    )}
                  </tr>
                );
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-4 text-ink-soft text-center">
                    {t("magics.empty")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Create form */}
        <h3 className="text-sm font-medium text-ink mt-6 mb-2">
          {t("magics.createHeading")}
        </h3>
        {createError !== null && (
          <p className="text-sm text-red-600 mb-2">{createError}</p>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-ink-soft">
            {t("magics.createNameLabel")}
            <input
              className="mt-1 block rounded border border-ink-soft/30 px-2 py-1 text-sm w-48"
              placeholder={t("magics.createNamePlaceholder")}
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void submitCreate(); }}
            />
          </label>
          <label className="text-xs text-ink-soft">
            {t("magics.createParentLabel")}
            <select
              className="mt-1 block rounded border border-ink-soft/30 px-2 py-1 text-sm"
              value={createParent}
              onChange={(e) => setCreateParent(e.target.value)}
            >
              <option value="">{t("magics.createParentNone")}</option>
              {(magics ?? []).map((m) => (
                <option key={m.id} value={String(m.id)}>{m.name}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={creating || !createName.trim()}
            className="rounded bg-ink text-paper px-3 py-1.5 text-sm disabled:opacity-50"
            onClick={() => void submitCreate()}
          >
            {creating ? "…" : t("common.add")}
          </button>
        </div>
      </ConsoleCard>
    </div>
  );
}
