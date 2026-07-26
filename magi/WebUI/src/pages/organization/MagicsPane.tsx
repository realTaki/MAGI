/**
 * MagicsPane — "MAGI 团体 / MAGIC (MAGI Council)" management pane.
 *
 * Full CRUD for the MAGIC team tree. Features:
 *   - Tree view with expand/collapse (DFS flatten, depth-indented rows).
 *   - Create new MAGIC team (name + optional parent).
 *   - Inline edit: rename, reparent, assign ADAM Magi.
 *   - Delete with cascade warning.
 */

import { useEffect, useMemo, useState } from "react";

import ConsoleCard from "../../components/ConsoleCard";
import { InfoTip } from "../../components/InfoTip";
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

function adamCandidates(magis: MagiBrief[]): MagiBrief[] {
  return magis.filter((m) => m.magic_position === "adam");
}

// -- pane -------------------------------------------------------------------

export function MagicsPane() {
  const t = useT();
  const [magics, setMagics] = useState<MAGICRow[] | null>(null);
  const [magis, setMagis] = useState<MagiBrief[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [createName, setCreateName] = useState("");
  const [createParent, setCreateParent] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<{
    name: string;
    parent_id: string;
    adam_id: string;
  }>({ name: "", parent_id: "", adam_id: "" });
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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
  const adams = useMemo(() => (magis ? adamCandidates(magis) : []), [magis]);

  const visibleIds = useMemo(() => {
    const vis = new Set<number>();
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
    const roots = (magics ?? []).filter(
      (r) => r.parent_id == null || !byId.has(r.parent_id),
    );
    const stack: { id: number; depth: number; children: MAGICRow[] }[] = [];
    for (const r of roots) {
      stack.push({ id: r.id, depth: 0, children: byId.get(r.id)!.children });
    }
    while (stack.length > 0) {
      const { id, children } = stack.pop()!;
      vis.add(id);
      if (expanded.has(id)) {
        for (const ch of [...children].reverse()) {
          stack.push({
            id: ch.id,
            depth: 0,
            children: byId.get(ch.id)!.children,
          });
        }
      }
    }
    return vis;
  }, [magics, expanded]);

  const visibleFlat = useMemo(
    () => flat.filter((f) => visibleIds.has(f.id)),
    [flat, visibleIds],
  );

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

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

  const deleteMagic = async (id: number, name: string) => {
    if (
      !confirm(
        t("magics.deleteConfirm") ||
          `Delete team "${name}"? This will cascade-delete its Magis.`,
      )
    )
      return;
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

  const rows = visibleFlat;
  const INDENT_PX = 20;

  return (
    <div className="space-y-4">
      <ConsoleCard
        title={t("magics.paneTitle")}
        headerRight={
          <div className="flex items-center gap-2">
            <InfoTip text={t("magics.paneDesc")} />
            <button
              type="button"
              className="btn btn-primary text-xs py-1.5 px-3"
              aria-expanded={createOpen}
              onClick={() => {
                setCreateOpen((open) => !open);
                setCreateError(null);
              }}
            >
              {createOpen ? t("common.cancel") : t("magics.createHeading")}
            </button>
          </div>
        }
      >
        {loadError !== null && (
          <p className="form-error mb-3">{loadError}</p>
        )}

        {createOpen && (
          <div className="mb-5 rounded-lg border border-sky-light/50 bg-sky-pale/15 p-4">
            <h3 className="text-sm font-medium text-ink mb-3">
              {t("magics.createHeading")}
            </h3>
            {createError !== null && (
              <p className="form-error mb-3">{createError}</p>
            )}
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1">
                <span className="form-label">
                  {t("magics.createNameLabel")}
                </span>
                <input
                  className="form-input text-sm py-1.5 px-3 w-40"
                  placeholder={t("magics.createNamePlaceholder")}
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submitCreate();
                  }}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="form-label">
                  {t("magics.createParentLabel")}
                </span>
                <select
                  className="form-input text-sm py-1.5 px-3"
                  value={createParent}
                  onChange={(e) => setCreateParent(e.target.value)}
                >
                  <option value="">{t("magics.createParentNone")}</option>
                  {(magics ?? []).map((m) => (
                    <option key={m.id} value={String(m.id)}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={creating || !createName.trim()}
                className="btn btn-primary text-sm py-1.5 px-4"
                onClick={() => void submitCreate()}
              >
                {creating ? t("common.loading") : t("common.add")}
              </button>
            </div>
          </div>
        )}

        {/* Tree table */}
        <div className="overflow-x-auto">
          <table className="data-table w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-3 w-8" />
                <th className="py-2 pr-3 font-medium">{t("magics.columnName")}</th>
                <th className="py-2 pr-3 font-medium">{t("magics.columnParent")}</th>
                <th className="py-2 pr-3 font-medium">{t("magics.columnAdam")}</th>
                <th className="py-2 pr-3 font-medium w-16 text-center">
                  {t("magics.columnChildren")}
                </th>
                <th className="py-2 pr-3 font-medium w-28 text-right" />
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => {
                const isEdit = editingId === m.id;
                const hasChildren = m.child_count > 0;
                const expandedRow = hasChildren && expanded.has(m.id);
                return (
                  <tr
                    key={m.id}
                    className={
                      "border-b border-sky-light/30 transition-colors " +
                      (isEdit ? "bg-sky-pale/20" : "hover:bg-sky-pale/10")
                    }
                  >
                    <td className="py-2 pr-0">
                      <button
                        type="button"
                        disabled={!hasChildren}
                        onClick={() => toggleExpand(m.id)}
                        className={
                          "w-5 h-5 inline-flex items-center justify-center rounded text-xs " +
                          (hasChildren
                            ? "text-ink-soft hover:bg-sky-light/60 hover:text-ink"
                            : "text-ink-soft/20 cursor-default")
                        }
                        aria-label={
                          expandedRow
                            ? t("sidebar.orgCollapseChildren")
                            : t("sidebar.orgExpandChildren")
                        }
                      >
                        {hasChildren ? (expandedRow ? "▾" : "▸") : ""}
                      </button>
                    </td>

                    {isEdit ? (
                      /* ── edit row ────────────────────────────── */
                      <>
                        <td
                          className="py-2 pr-3"
                          colSpan={hasChildren ? 4 : 4}
                        >
                          <div className="flex items-center gap-2 ml-1">
                            <input
                              className="form-input text-sm py-1 px-2 w-32"
                              value={editDraft.name}
                              onChange={(e) =>
                                setEditDraft((d) => ({
                                  ...d,
                                  name: e.target.value,
                                }))
                              }
                            />
                            <select
                              className="form-input text-sm py-1 px-2"
                              value={editDraft.parent_id}
                              onChange={(e) =>
                                setEditDraft((d) => ({
                                  ...d,
                                  parent_id: e.target.value,
                                }))
                              }
                            >
                              <option value="">
                                {t("magics.createParentNone")}
                              </option>
                              {magics
                                ?.filter((x) => x.id !== m.id)
                                .map((x) => (
                                  <option key={x.id} value={String(x.id)}>
                                    {x.name}
                                  </option>
                                ))}
                            </select>
                            <select
                              className="form-input text-sm py-1 px-2"
                              value={editDraft.adam_id}
                              onChange={(e) =>
                                setEditDraft((d) => ({
                                  ...d,
                                  adam_id: e.target.value,
                                }))
                              }
                            >
                              <option value="">
                                {t("magics.editAdamNone")}
                              </option>
                              {adams.map((a) => (
                                <option key={a.id} value={String(a.id)}>
                                  #{a.id} ({a.provider ?? "?"})
                                </option>
                              ))}
                            </select>
                            <button
                              type="button"
                              disabled={saving}
                              onClick={() => void submitEdit(m.id)}
                              className="btn btn-primary text-xs py-1 px-3"
                            >
                              {saving ? "…" : t("common.save")}
                            </button>
                            <button
                              type="button"
                              onClick={cancelEdit}
                              className="btn btn-secondary text-xs py-1 px-2"
                            >
                              {t("common.cancel")}
                            </button>
                            {editError && (
                              <span className="text-xs text-rose-600">
                                {editError}
                              </span>
                            )}
                          </div>
                        </td>
                        <td />
                      </>
                    ) : (
                      /* ── view row ────────────────────────────── */
                      <>
                        <td
                          className="py-2 pr-3"
                          style={{ paddingLeft: `${8 + m.depth * INDENT_PX}px` }}
                        >
                          <span className="font-medium text-ink">
                            {m.depth > 0 && (
                              <span className="text-ink-soft/50 mr-1.5 select-none">
                                {expandedRow ? "└┬" : "├─"}
                              </span>
                            )}
                            {m.name}
                          </span>
                          <span className="text-ink-soft/50 font-mono text-[11px] ml-2">
                            #{m.id}
                          </span>
                        </td>
                        <td className="py-2 pr-3 font-mono text-xs text-ink-soft">
                          {m.parent_id != null
                            ? (magics?.find((x) => x.id === m.parent_id)
                                ?.name ?? `#${m.parent_id}`)
                            : "—"}
                        </td>
                        <td className="py-2 pr-3">
                          {m.adam_id != null ? (
                            <span className="status-pill status-pill--connected text-[11px]">
                              ADAM #{m.adam_id}
                            </span>
                          ) : (
                            <span className="text-ink-soft text-xs">—</span>
                          )}
                        </td>
                        <td className="py-2 pr-3 font-mono text-xs text-ink-soft text-center">
                          {m.child_count}
                        </td>
                        <td className="py-2 pr-3 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => startEdit(m)}
                              className="btn btn-secondary text-xs py-1 px-2"
                            >
                              {t("common.edit")}
                            </button>
                            <button
                              type="button"
                              onClick={() => void deleteMagic(m.id, m.name)}
                              className="btn btn-secondary text-xs py-1 px-2 text-rose-600 hover:text-rose-800"
                            >
                              {t("common.delete")}
                            </button>
                          </div>
                        </td>
                      </>
                    )}
                  </tr>
                );
              })}
              {rows.length === 0 && !loadError && (
                <tr>
                  <td
                    colSpan={6}
                    className="py-6 text-ink-soft text-sm text-center"
                  >
                    {t("magics.empty")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </ConsoleCard>
    </div>
  );
}
