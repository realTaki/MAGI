/**
 * DepartmentsPane — the "部门管理" half of the Organization tab.
 *
 * CRUD for departments. The backend returns departments
 * as a flat list with ``parent_id``; the frontend builds
 * a parent → children map and DFS-renders so the tree
 * structure is visible in the table. Create / edit use a
 * single shared form (collapsed by default), so switching
 * between "new" and "edit <id>" is just a state change.
 *
 * Tree helpers (``buildTree`` / ``flattenTree``) live
 * with the pane because nothing else uses them — the
 * Employees pane renders a flat list and doesn't need DFS.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../../components/ConsoleCard";
import { useT } from "../../i18n/index";
import type { DepartmentRow, EmployeeRow } from "../OrganizationTab";

// (the API also refuses, but the UI gate saves a round-trip).
type FlatDept = DepartmentRow & { depth: number; children: FlatDept[] };

function buildTree(rows: DepartmentRow[]): FlatDept[] {
  const byId = new Map<number, FlatDept>();
  for (const r of rows) {
    byId.set(r.id, { ...r, depth: 0, children: [] });
  }
  const roots: FlatDept[] = [];
  for (const r of rows) {
    const node = byId.get(r.id)!;
    if (r.parent_id != null && byId.has(r.parent_id)) {
      byId.get(r.parent_id)!.children.push(node);
    } else {
      // Either top-level or parent_id references a missing row —
      // promote to root so the row stays visible.
      roots.push(node);
    }
  }
  const assignDepth = (nodes: FlatDept[], d: number) => {
    for (const n of nodes) {
      n.depth = d;
      assignDepth(n.children, d + 1);
    }
  };
  assignDepth(roots, 0);
  return roots;
}

function flattenTree(
  roots: FlatDept[],
  collapsed: ReadonlySet<number>,
  out: FlatDept[] = [],
): FlatDept[] {
  for (const n of roots) {
    out.push(n);
    // When the node is collapsed, skip its subtree entirely.
    // The node itself stays in the list so the operator can
    // click again to re-expand.
    if (n.children.length && !collapsed.has(n.id)) {
      flattenTree(n.children, collapsed, out);
    }
  }
  return out;
}

export function DepartmentsPane() {
  const t = useT();
  const [departments, setDepartments] = useState<DepartmentRow[] | null>(null);
  const [employees, setEmployees] = useState<EmployeeRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Set of dept IDs whose subtree is currently folded up in the
  // table. Rows without children don't get a chevron and don't
  // need to be in this set. Defaults to empty = everything
  // expanded, so the table matches the previous behaviour until
  // the user starts folding.
  const [collapsed, setCollapsed] = useState<Set<number>>(
    () => new Set(),
  );

  function toggleCollapsed(id: number) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  // Form state — null when collapsed. ``editingId === null`` +
  // ``addingNew`` means "create mode".
  const [editingId, setEditingId] = useState<number | null>(null);
  const [addingNew, setAddingNew] = useState(false);

  // Default parent when the form opens via the "+ 子部门"
  // button. ``null`` means "no default" (top-level form from
  // the top button).
  const [formDefaultParent, setFormDefaultParent] = useState<number | null>(null);
  const [form, setForm] = useState<{
    name: string;
    parent_id: number | null;
    manager_id: number | null;
  }>({ name: "", parent_id: null, manager_id: null });
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function refresh() {
    setLoadError(null);
    try {
      const [d, e] = await Promise.all([
        fetch("/api/departments", { credentials: "include" }),
        fetch("/api/employees", { credentials: "include" }),
      ]);
      if (!d.ok || !e.ok) {
        setLoadError(
          `Failed to load (departments ${d.status}, employees ${e.status})`,
        );
        return;
      }
      setDepartments(await d.json());
      setEmployees(await e.json());
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  function openCreate() {
    setForm({ name: "", parent_id: formDefaultParent, manager_id: null });
    setEditingId(null);
    setAddingNew(true);
    setFormError(null);
  }

  // Open the create form pre-filled with ``parent_id`` = the
  // row the user clicked. Called by the per-row "+ 子部门"
  // button and by the detail panel's "创建下级部门" button.
  function openCreateChild(parentId: number) {
    setFormDefaultParent(parentId);
    setForm({ name: "", parent_id: parentId, manager_id: null });
    setEditingId(null);
    setAddingNew(true);
    setFormError(null);
  }

  function openEdit(d: DepartmentRow) {
    setFormDefaultParent(null);
    setForm({
      name: d.name,
      parent_id: d.parent_id,
      manager_id: d.manager?.id ?? null,
    });
    setEditingId(d.id);
    setAddingNew(false);
    setFormError(null);
  }

  function closeForm() {
    setEditingId(null);
    setAddingNew(false);
    setFormError(null);
    setForm({ name: "", parent_id: null, manager_id: null });
    setFormDefaultParent(null);
  }

  async function save() {
    const name = form.name.trim();
    if (!name) {
      setFormError("部门名称不能为空");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const url = editingId
        ? `/api/departments/${editingId}`
        : "/api/departments";
      const method = editingId ? "PATCH" : "POST";
      const body = {
        name,
        parent_id: form.parent_id,
        manager_id: form.manager_id,
      };
      const r = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        credentials: "include",
      });
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        setFormError(detail.detail ?? `${method} failed (${r.status})`);
        return;
      }
      closeForm();
      await refresh();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  async function remove(d: DepartmentRow) {
    if (d.child_count > 0) {
      alert(
        `「${d.name}」有 ${d.child_count} 个子部门，请先删除子部门`,
      );
      return;
    }
    if (!confirm(`确定删除「${d.name}」？此操作不可撤销。`)) return;
    const r = await fetch(`/api/departments/${d.id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!r.ok && r.status !== 204) {
      const detail = (await r.json().catch(() => ({}))) as {
        detail?: string;
      };
      alert(detail.detail ?? `Delete failed (${r.status})`);
      return;
    }
    if (editingId === d.id) closeForm();
    await refresh();
  }

  const formOpen = addingNew || editingId !== null;
  const tree = departments ? buildTree(departments) : [];
  const flat = flattenTree(tree, collapsed);

  // The parent dropdown should offer "no parent" (top-level) plus
  // every other department EXCEPT the one being edited (a dept
  // can't be its own parent). The "tree" option in v2 would
  // render a hierarchical picker; the flat list with leading
  // em-spaces is good enough for C1.1.
  const parentOptions = (departments ?? []).filter(
    (d) => d.id !== editingId,
  );

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-ink">部门管理</h2>
          <p className="mt-1 text-sm text-ink-soft">
            树形组织结构。每个部门可以指定负责人，子部门通过
            「上级部门」字段挂在父节点下。
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={openCreate}
            disabled={formOpen && !addingNew}
            className="btn btn-primary px-4 py-2"
          >
            + Create department
          </button>
        </div>
      </div>

      {formOpen && (
        <ConsoleCard title={addingNew ? "新建部门" : "编辑部门"}>
          <div className="space-y-3">
            <div>
              <label htmlFor="dept-name" className="form-label">
                部门名称
              </label>
              <input
                id="dept-name"
                type="text"
                value={form.name}
                onChange={(e) =>
                  setForm((f) => ({ ...f, name: e.target.value }))
                }
                placeholder="例如：Engineering"
                className="form-input text-sm py-2 px-3"
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="dept-parent" className="form-label">
                  上级部门
                </label>
                <select
                  id="dept-parent"
                  value={form.parent_id ?? ""}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      parent_id: e.target.value === "" ? null : Number(e.target.value),
                    }))
                  }
                  className="form-input text-sm py-2 px-3"
                >
                  <option value="">（无 — 根部门）</option>
                  {parentOptions.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="dept-manager" className="form-label">
                  负责人
                </label>
                <select
                  id="dept-manager"
                  value={form.manager_id ?? ""}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      manager_id: e.target.value === "" ? null : Number(e.target.value),
                    }))
                  }
                  className="form-input text-sm py-2 px-3"
                >
                  <option value="">（无）</option>
                  {(employees ?? []).map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.display_name || e.name}
                    </option>
                  ))}
                </select>
                {(employees ?? []).length === 0 && (
                  <p className="mt-1 text-xs text-ink-soft">
                    还没有员工。切到「员工管理」先创建。
                  </p>
                )}
              </div>
            </div>

            {formError && (
              <p className="form-error">✗ {formError}</p>
            )}

            {/* All form actions live in one row, separated visually
                by a thin gap. Edit-mode-only ops (创建下级部门 /
                删除部门) come first, then 保存 / 取消 at the end
                with ``ml-auto`` so they push to the right. In
                create mode the edit-ops block is skipped, leaving
                just 保存 / 取消 on the right. */}
            {(() => {
              const editing = !addingNew
                ? (departments ?? []).find((d) => d.id === editingId) ?? null
                : null;
              return (
                <div className="flex items-center gap-2 pt-3 border-t border-sky-light/40 flex-wrap">
                  {editing && (
                    <>
                      <button
                        type="button"
                        onClick={() => openCreateChild(editing.id)}
                        disabled={saving}
                        className="btn btn-primary text-sm py-1.5 px-3"
                      >
                        + 创建下级部门
                      </button>
                      <button
                        type="button"
                        onClick={() => remove(editing)}
                        disabled={saving || editing.child_count > 0}
                        title={
                          editing.child_count > 0
                            ? `有 ${editing.child_count} 个子部门，必须先全部删除`
                            : "删除部门"
                        }
                        className="btn btn-danger text-sm py-1.5 px-3"
                      >
                        删除部门
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    onClick={save}
                    disabled={saving}
                    className={`btn btn-primary text-sm py-1.5 px-4 ${editing ? "ml-auto" : ""}`}
                  >
                    {saving ? "保存中…" : "保存"}
                  </button>
                  <button
                    type="button"
                    onClick={closeForm}
                    disabled={saving}
                    className="btn btn-secondary text-sm py-1.5 px-4"
                  >
                    取消
                  </button>
                </div>
              );
            })()}
          </div>
        </ConsoleCard>
      )}

      <ConsoleCard title="">
        {loadError && (
          <p className="form-error mb-3">✗ {loadError}</p>
        )}
        {departments === null && !loadError && (
          <p className="text-sm text-ink-soft">Loading…</p>
        )}
        {departments !== null && departments.length === 0 && (
          <p className="form-empty">
            还没有部门。点 + Create department 开始。
          </p>
        )}
        {departments !== null && departments.length > 0 && (
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">部门名称</th>
                <th className="py-2 pr-4 font-medium w-24">子部门数</th>
                <th className="py-2 pr-4 font-medium">负责人</th>
                <th className="py-2 font-medium w-28 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {flat.map((d) => {
                const isEditing = editingId === d.id;
                const hasChildren = d.child_count > 0;
                const isCollapsed = collapsed.has(d.id);
                return (
                  <tr
                    key={d.id}
                    className={
                      "border-b border-sky-light/30 last:border-0 " +
                      (isEditing ? "bg-sky-50/50" : "")
                    }
                  >
                    <td className="py-2 pr-4 text-ink">
                      <span
                        style={{ paddingLeft: `${d.depth * 20}px` }}
                        className="inline-flex items-center gap-1"
                      >
                        {hasChildren ? (
                          <button
                            type="button"
                            onClick={() => toggleCollapsed(d.id)}
                            title={isCollapsed ? "展开子部门" : "收起子部门"}
                            aria-label={
                              isCollapsed
                                ? t("sidebar.orgExpandChildren")
                                : t("sidebar.orgCollapseChildren")
                            }
                            className="inline-flex items-center justify-center w-4 h-4 text-sky-deep hover:text-ocean transition"
                          >
                            {/* ▼ when expanded, ▶ when collapsed */}
                            <span
                              className="inline-block text-[10px] leading-none transition-transform"
                              style={{
                                transform: isCollapsed
                                  ? "rotate(0deg)"
                                  : "rotate(90deg)",
                              }}
                            >
                              ▶
                            </span>
                          </button>
                        ) : (
                          // Spacer so leaf rows line up with parent rows.
                          <span className="inline-block w-4" />
                        )}
                        <span className="font-medium">{d.name}</span>
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-ink-soft">
                      {d.child_count}
                    </td>
                    <td className="py-2 pr-4 text-ink-soft">
                      {d.manager ? (
                        d.manager.display_name || d.manager.name
                      ) : (
                        <span className="text-ink-soft">—</span>
                      )}
                    </td>
                    <td className="py-2 text-right space-x-2">
                      <button
                        type="button"
                        onClick={() => openEdit(d)}
                        disabled={formOpen && !isEditing}
                        className="text-xs text-sky-700 hover:text-sky-deep transition disabled:text-sky-light/50 disabled:cursor-not-allowed"
                      >
                        编辑
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </ConsoleCard>
    </div>
  );
}
