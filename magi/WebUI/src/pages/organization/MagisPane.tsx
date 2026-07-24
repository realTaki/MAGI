/**
 * MagisPane — the "智能体管理 / magis 管理" half of the Organization tab.
 *
 * Replaces the old "部门管理 (Departments)" section in the post-refactor
 * reframe. Two stacked lists:
 *
 *   1. **MAGI 团队 (MAGICs)** — the tree of councils. Each row has
 *      ``name``, ``parent_id`` (tree shape kept for v0), ``adam_id``
 *      (the manager Magi for this team), and ``child_count``. The
 *      "tree" rendering uses the same DFS flatten the old
 *      ``DepartmentsPane`` used so the column ordering + indent
 *      behaviour matches the operator's prior muscle memory.
 *   2. **智能体 (Magis)** — the per-MAGI agent rows under each
 *      council. Each row carries ``magic_position`` (``adam`` /
 *      ``eve``), ``provider``, ``api_key_set`` / ``api_key_last4``.
 *      The "create" form lives at the bottom and accepts the
 *      ``magic_id`` + ``magic_position`` minimum; provider / key
 *      come later via the detail panel.
 *
 * Reads from ``GET /api/magics`` and ``GET /api/magis`` — both
 * shipped by the new ``magi.channels.webui.api.magis`` router.
 * No ``department_id`` anywhere; the legacy org sub-tree concept
 * is gone.
 */

import { useEffect, useMemo, useState } from "react";

import ConsoleCard from "../../components/ConsoleCard";
import { useT } from "../../i18n/index";

// -- backend wire shapes (mirror magi/channels/webui/api/magis.py) --------

export type MAGICRow = {
  id: number;
  name: string;
  parent_id: number | null;
  adam_id: number | null;
  child_count: number;
  created_at: string;
  updated_at: string;
};

export type MagiRow = {
  id: number;
  magic_id: number;
  magic_position: string; // "adam" | "eve"
  provider: string | null;
  api_key_set: boolean;
  api_key_last4: string | null;
  created_at: string;
  updated_at: string;
};

// -- tree flatten (matches the old DepartmentsPane contract) ---------------

type FlatMAGIC = MAGICRow & { depth: number };

function flattenTree(
  rows: MAGICRow[],
): FlatMAGIC[] {
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
      // Either top-level or parent_id references a missing row.
      // Promote to root so the row stays visible.
      roots.push(node);
    }
  }
  // Sort siblings by name for stable display.
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

// -- pane ------------------------------------------------------------------

export function MagisPane() {
  const t = useT();
  const [magics, setMagics] = useState<MAGICRow[] | null>(null);
  const [magis, setMagis] = useState<MagiRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Inline create-magi form state.
  const [addForm, setAddForm] = useState<{
    magic_id: string;
    magic_position: "adam" | "eve";
    provider: string;
    api_key: string;
  }>({ magic_id: "", magic_position: "eve", provider: "", api_key: "" });
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  // Refresh both lists.
  const reload = async () => {
    setLoadError(null);
    try {
      const [mr, gi] = await Promise.all([
        fetch("/api/magics", { credentials: "include" }),
        fetch("/api/magis", { credentials: "include" }),
      ]);
      if (!mr.ok || !gi.ok) {
        setLoadError(
          `failed to load (magics=${mr.status}, magis=${gi.status})`,
        );
        return;
      }
      setMagics((await mr.json()) as MAGICRow[]);
      setMagis((await gi.json()) as MagiRow[]);
    } catch (e) {
      setLoadError(`network error: ${(e as Error).message}`);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const flat = useMemo(
    () => (magics ? flattenTree(magics) : []),
    [magics],
  );

  // Group magis by magic_id so the table reads "team N: <rows>".
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
    if (!confirm(`Delete magi #${id}?`)) return;
    const res = await fetch(`/api/magis/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (res.ok) await reload();
  };

  return (
    <div className="space-y-4">
      <ConsoleCard title={t("magis.paneTitle") || "智能体管理"}>
        {loadError !== null && (
          <p className="text-sm text-red-600 mb-2">{loadError}</p>
        )}

        {/* MAGI 团队 (MAGICs) table — the tree of councils. */}
        <h3 className="text-sm font-medium text-ink mt-2 mb-2">
          {t("magis.magicsHeading") || "MAGI 团队"}
        </h3>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-ink-soft border-b border-ink-soft/20">
                <th className="py-2 pr-3">name</th>
                <th className="py-2 pr-3">id</th>
                <th className="py-2 pr-3">parent</th>
                <th className="py-2 pr-3">adam</th>
                <th className="py-2 pr-3">children</th>
              </tr>
            </thead>
            <tbody>
              {flat.map((m) => (
                <tr
                  key={m.id}
                  className="border-b border-ink-soft/10"
                >
                  <td className="py-1.5 pr-3" style={{ paddingLeft: `${0.75 + m.depth * 1.5}rem` }}>
                    {m.depth > 0 && <span className="text-ink-soft mr-1">└─</span>}
                    {m.name}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-ink-soft">{m.id}</td>
                  <td className="py-1.5 pr-3 font-mono text-ink-soft">
                    {m.parent_id ?? "—"}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-ink-soft">
                    {m.adam_id ?? "—"}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-ink-soft">
                    {m.child_count}
                  </td>
                </tr>
              ))}
              {flat.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="py-2 text-ink-soft text-center"
                  >
                    (no MAGIC rows)
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* 智能体 (Magis) table — flat list grouped by magic_id. */}
        <h3 className="text-sm font-medium text-ink mt-6 mb-2">
          {t("magis.magisHeading") || "智能体 (Magis)"}
        </h3>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-ink-soft border-b border-ink-soft/20">
                <th className="py-2 pr-3">id</th>
                <th className="py-2 pr-3">magic_id</th>
                <th className="py-2 pr-3">position</th>
                <th className="py-2 pr-3">provider</th>
                <th className="py-2 pr-3">api_key</th>
                <th className="py-2 pr-3"></th>
              </tr>
            </thead>
            <tbody>
              {(magis ?? []).map((m) => (
                <tr key={m.id} className="border-b border-ink-soft/10">
                  <td className="py-1.5 pr-3 font-mono text-ink-soft">{m.id}</td>
                  <td className="py-1.5 pr-3 font-mono text-ink-soft">{m.magic_id}</td>
                  <td className="py-1.5 pr-3">
                    <span
                      className={
                        m.magic_position === "adam"
                          ? "px-2 py-0.5 rounded bg-amber-100 text-amber-800 text-xs"
                          : "px-2 py-0.5 rounded bg-sky-100 text-sky-800 text-xs"
                      }
                    >
                      {m.magic_position}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3">{m.provider ?? "—"}</td>
                  <td className="py-1.5 pr-3 font-mono text-ink-soft">
                    {m.api_key_set
                      ? `…${m.api_key_last4 ?? ""}`
                      : "(not set)"}
                  </td>
                  <td className="py-1.5 pr-3">
                    <button
                      type="button"
                      onClick={() => void deleteMagi(m.id)}
                      className="text-xs text-red-600 hover:underline"
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
              {magis !== null && magis.length === 0 && (
                <tr>
                  <td
                    colSpan={6}
                    className="py-2 text-ink-soft text-center"
                  >
                    (no magi rows)
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Per-MAGI breakdown (how many agents each team owns). */}
        <h3 className="text-sm font-medium text-ink mt-6 mb-2">
          per-MAGI breakdown
        </h3>
        <ul className="text-sm text-ink-soft space-y-0.5">
          {Array.from(magisByMagic.entries()).map(([mid, list]) => (
            <li key={mid}>
              <span className="font-mono">magic #{mid}</span>
              {" — "}
              {list.length} agent{list.length === 1 ? "" : "s"}
              {" ("}
              {list.map((x) => x.magic_position).join(", ")}
              {")"}
            </li>
          ))}
          {magisByMagic.size === 0 && <li>(none)</li>}
        </ul>

        {/* Create form. */}
        <h3 className="text-sm font-medium text-ink mt-6 mb-2">
          {t("magis.createHeading") || "新建智能体"}
        </h3>
        {addError !== null && (
          <p className="text-sm text-red-600 mb-2">{addError}</p>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-5 gap-2 items-end">
          <label className="text-xs text-ink-soft">
            magic_id
            <input
              type="number"
              min={1}
              className="mt-1 block w-full rounded border border-ink-soft/30 px-2 py-1 text-sm"
              value={addForm.magic_id}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, magic_id: e.target.value }))
              }
            />
          </label>
          <label className="text-xs text-ink-soft">
            position
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
              <option value="eve">eve</option>
              <option value="adam">adam</option>
            </select>
          </label>
          <label className="text-xs text-ink-soft">
            provider
            <input
              className="mt-1 block w-full rounded border border-ink-soft/30 px-2 py-1 text-sm"
              value={addForm.provider}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, provider: e.target.value }))
              }
            />
          </label>
          <label className="text-xs text-ink-soft">
            api_key
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
            disabled={adding}
            className="rounded bg-ink text-paper px-3 py-1.5 text-sm disabled:opacity-50"
            onClick={() => void submitCreate()}
          >
            {adding ? "creating…" : "create"}
          </button>
        </div>
      </ConsoleCard>
    </div>
  );
}