/**
 * EmployeesPane — the "员工管理" half of the Organization tab.
 *
 * Master-detail: left sidebar lists departments + a
 * "未指定部门" pseudo-item + a 已离职 view; right pane
 * shows employees in the selected scope. Clicking
 * 查看详情 on a row opens an inline detail panel for
 * the LLM provider / API key configuration.
 *
 * Provider options, scope tagged-union, and the
 * paginated response type live with the pane because
 * nothing else consumes them — the Departments pane
 * doesn't render employees and doesn't need providers.
 */

import { useEffect, useRef, useState } from "react";

import ConsoleCard from "../../components/ConsoleCard";
import { useT } from "../../i18n/index";
import type { DepartmentRow, EmployeeRow } from "../OrganizationTab";

type EmployeeListResponse = {
  items: EmployeeRow[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

// Master-detail "scope" — what the right pane is showing.
//   - "unassigned"  : employees with no department
//   - "department"  : employees in a specific dept
//   - "separated"   : the dedicated 已离职员工 view (across depts)
type EmployeeScope =
  | { kind: "unassigned" }
  | { kind: "department"; departmentId: number }
  | { kind: "separated" };

// Mirrors the backend's
// ``magi.agent.llm.factory.provider_options_for_ui()``.
// v0 ships the Anthropic-API-compatible family
// (Claude + the two Minimax regions); OpenAI / Ollama
// / etc. land as their providers come online — add a
// row here AND the branch in the backend factory so
// the picker and the validator stay in sync.
const PROVIDER_OPTIONS = [
  { value: "", label: "（未指定）" },
  { value: "claude", label: "Anthropic (Claude)" },
  { value: "minimax-global", label: "Minimax (Global)" },
  { value: "minimax-cn", label: "Minimax (China)" },
] as const;

// Build a DFS-ordered list of departments with each row's depth,
// so the renderer can indent by depth. The backend's
// ``child_count`` is the number of direct sub-departments; we
// use it both for display and to disable Delete on non-leaves
// (the API also refuses, but the UI gate saves a round-trip).

export function EmployeesPane() {
  const t = useT();
  const [departments, setDepartments] = useState<DepartmentRow[] | null>(null);
  // ``employeeList`` is the full paginated response; the table
  // renders ``employeeList.items`` while the pager reads the
  // totals off the same object.
  const [employeeList, setEmployeeList] = useState<EmployeeListResponse | null>(
    null,
  );
  // Page index (1-based). Reset to 1 whenever the scope or
  // the include_separated toggle changes — see the effect
  // below. ``total_pages`` on the response clamps us.
  const [page, setPage] = useState(1);
  // "Show separated employees in this scope" toggle. Applies
  // to ``unassigned`` and ``department`` scopes only — the
  // dedicated ``separated`` scope always shows them.
  const [includeSeparated, setIncludeSeparated] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // The selected sidebar item. Defaults to "unassigned" — new
  // operators usually start by adding people without a dept
  // and then creating a dept to drag them into.
  const [scope, setScope] = useState<EmployeeScope>({ kind: "unassigned" });

  // Inline "add employee" form, collapsed by default.
  const [addingNew, setAddingNew] = useState(false);
  const [addForm, setAddForm] = useState<{
    name: string;
    display_name: string;
    department_id: number | null;
  }>({ name: "", display_name: "", department_id: null });
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  // The currently-viewed employee detail panel. ``null`` means
  // no panel open. The detail panel is always editable; the
  // form fields are seeded from the employee's current state
  // when the panel opens.
  const [viewingId, setViewingId] = useState<number | null>(null);
  const [detailForm, setDetailForm] = useState<{
    display_name: string;
    department_id: number | null;
    provider: string;
    api_key: string;
    role: "admin" | "assigned" | "employee" | "guest";
    telegram_id: string; // string in the form (input); we
    // convert to number | null on submit.
  }>({
    display_name: "",
    department_id: null,
    provider: "",
    api_key: "",
    role: "employee",
    telegram_id: "",
  });
  const [detailError, setDetailError] = useState<string | null>(null);
  const [savingDetail, setSavingDetail] = useState(false);
  // D.15 — token-usage for the currently-viewed employee.
  // Loaded on detail-panel open; cleared on close. Three
  // periods in one response (week / month / total) so the
  // panel renders all three rows in a single render pass
  // — no waterfall, no separate useEffects.
  type TokenUsagePeriod = {
    input_tokens: number;
    output_tokens: number;
    call_count: number;
    period_start: string;
    period_end: string;
  };
  type TokenUsageOut = {
    uid: number;
    week: TokenUsagePeriod;
    month: TokenUsagePeriod;
    total: TokenUsagePeriod;
    timezone: string;
  };
  const [tokenUsage, setTokenUsage] = useState<TokenUsageOut | null>(null);
  const [tokenUsageError, setTokenUsageError] = useState<string | null>(null);

  // -- fetches ------------------------------------------------------------

  async function refreshDepartments() {
    try {
      const r = await fetch("/api/departments", { credentials: "include" });
      if (r.ok) setDepartments(await r.json());
    } catch {
      /* leave the previous value; the row-level error catches it */
    }
  }

  async function refreshEmployees() {
    setLoadError(null);
    try {
      const params = new URLSearchParams();
      if (scope.kind === "unassigned") {
        params.set("unassigned", "true");
      } else if (scope.kind === "department") {
        params.set("department_id", String(scope.departmentId));
      } else {
        params.set("separated", "true");
      }
      if (scope.kind !== "separated" && includeSeparated) {
        params.set("include_separated", "true");
      }
      params.set("page", String(page));
      const qs = `?${params.toString()}`;
      const r = await fetch(`/api/employees${qs}`, { credentials: "include" });
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      setEmployeeList((await r.json()) as EmployeeListResponse);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  // Re-fetch on mount + whenever scope / page / include_separated
  // changes. ``refreshEmployees`` reads those three from the
  // closure; the effect's dep list keeps them honest.
  useEffect(() => {
    void refreshDepartments();
  }, []);
  useEffect(() => {
    void refreshEmployees();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, page, includeSeparated]);

  // -- helpers ------------------------------------------------------------

  function unassignedCount(): number {
    // The list endpoint returns the page slice; we only know
    // the true unassigned total when that's the active scope.
    if (scope.kind === "unassigned") {
      return employeeList?.total ?? 0;
    }
    return -1;
  }

  function deptHeadcount(deptId: number): number {
    if (scope.kind === "department" && scope.departmentId === deptId) {
      return employeeList?.total ?? 0;
    }
    return -1;
  }

  function separatedCount(): number {
    if (scope.kind === "separated") {
      return employeeList?.total ?? 0;
    }
    return -1;
  }

  function selectScope(next: EmployeeScope) {
    setScope(next);
    setPage(1); // reset pager on scope change
    setViewingId(null); // close the detail panel on scope change
  }

  function toggleIncludeSeparated(next: boolean) {
    setIncludeSeparated(next);
    setPage(1); // toggling may add/remove rows; reset pager
  }

  // -- add employee -------------------------------------------------------

  function openAdd() {
    // Seed the form's department to whatever the current scope
    // is, so adding a new employee while looking at a dept
    // preselects that dept.
    const seedDeptId =
      scope.kind === "department" ? scope.departmentId : null;
    setAddForm({ name: "", display_name: "", department_id: seedDeptId });
    setAddError(null);
    setAddingNew(true);
  }

  function closeAdd() {
    setAddingNew(false);
    setAddError(null);
    setAddForm({ name: "", display_name: "", department_id: null });
  }

  async function submitAdd() {
    const name = addForm.name.trim();
    if (!name) {
      setAddError("姓名不能为空");
      return;
    }
    setAdding(true);
    setAddError(null);
    try {
      const r = await fetch("/api/employees", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          display_name: addForm.display_name.trim() || null,
          department_id: addForm.department_id,
        }),
        credentials: "include",
      });
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        setAddError(detail.detail ?? `Save failed (${r.status})`);
        return;
      }
      closeAdd();
      await refreshEmployees();
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Network error");
    } finally {
      setAdding(false);
    }
  }

  // -- detail panel -------------------------------------------------------

  function openDetail(emp: EmployeeRow) {
    setViewingId(emp.id);
    setDetailForm({
      display_name: emp.display_name ?? "",
      department_id: emp.department_id,
      provider: emp.provider ?? "",
      api_key: "", // never pre-fill; user re-enters to set/rotate
      role: emp.role,
      telegram_id: emp.telegram_id !== null ? String(emp.telegram_id) : "",
    });
    setDetailError(null);
    // D.15 — kick off the token-usage fetch in the same
    // tick. The fetch is fire-and-forget; a slow DB just
    // means the "Loading…" placeholder sticks around a
    // bit longer. We don't ``await`` so the detail panel
    // can paint immediately with the rest of the form.
    void loadTokenUsage(emp.id);
  }

  function closeDetail() {
    setViewingId(null);
    setDetailError(null);
    setTokenUsage(null);
    setTokenUsageError(null);
  }

  async function loadTokenUsage(empId: number) {
    setTokenUsage(null);
    setTokenUsageError(null);
    try {
      const r = await fetch(`/api/employees/${empId}/token-usage`, {
        credentials: "include",
      });
      if (!r.ok) {
        setTokenUsageError(`Failed to load (${r.status})`);
        return;
      }
      const body = (await r.json()) as TokenUsageOut;
      // Guard against a race: if the operator closed the
      // panel and opened another employee between fetch
      // start and resolve, don't paint stale numbers.
      // (Cheap because the close cleared the state.)
      if (viewingIdRef.current === empId) {
        setTokenUsage(body);
      }
    } catch (err) {
      setTokenUsageError(err instanceof Error ? err.message : "Network error");
    }
  }

  // Lightweight ref mirror of ``viewingId`` so the async
  // fetcher can check "is the panel still on this employee?"
  // without the closure-staleness that ``useState`` would
  // introduce. The fetcher schedules before this ref is
  // necessarily the latest; the guard is a soft check
  // (won't false-positive on a quick re-open of the same
  // employee, but that's the desired UX).
  const viewingIdRef = useRef<number | null>(null);
  useEffect(() => {
    viewingIdRef.current = viewingId;
  }, [viewingId]);

  async function submitDetail() {
    if (viewingId === null) return;
    setSavingDetail(true);
    setDetailError(null);
    try {
      const body: Record<string, unknown> = {
        display_name: detailForm.display_name.trim() || null,
        department_id: detailForm.department_id,
        provider: detailForm.provider || null,
        role: detailForm.role,
      };
      // Only send api_key when the user actually typed something
      // — empty string would clear the stored key (intentional
      // for rotate, but ``null`` means "don't change" so the
      // default PATCH semantics keep an existing key).
      if (detailForm.api_key !== "") {
        body.api_key = detailForm.api_key;
      }
      // Telegram id: empty string in the form means "unbind"
      // (set to null on the server); a numeric string is
      // converted to int. Whitespace-only input is treated
      // as empty.
      const tgRaw = detailForm.telegram_id.trim();
      if (tgRaw === "") {
        body.telegram_id = null;
      } else {
        const tgNum = Number(tgRaw);
        if (!Number.isInteger(tgNum)) {
          setDetailError("Telegram tgid 必须是整数");
          setSavingDetail(false);
          return;
        }
        body.telegram_id = tgNum;
      }
      const r = await fetch(`/api/employees/${viewingId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        credentials: "include",
      });
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        setDetailError(detail.detail ?? `Save failed (${r.status})`);
        return;
      }
      closeDetail();
      await refreshEmployees();
      await refreshDepartments();
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSavingDetail(false);
    }
  }

  // Soft-delete toggle on the detail panel. ``separated=true``
  // stamps ``separated_at = now``; ``separated=false`` clears it.
  // The endpoint uses ``model_fields_set`` semantics so we always
  // send the field — no "don't touch" branch needed here.
  async function toggleSeparated() {
    if (viewingId === null || !viewingEmp) return;
    const next = !viewingEmp.separated_at;
    const label = next ? "标记为离职" : "恢复在职";
    if (
      !confirm(
        next
          ? `确定把「${viewingEmp.name}」标记为离职吗？此操作可在详情里撤销。`
          : `确定把「${viewingEmp.name}」恢复为在职吗？`,
      )
    ) {
      return;
    }
    setSavingDetail(true);
    setDetailError(null);
    try {
      const r = await fetch(`/api/employees/${viewingId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ separated: next }),
        credentials: "include",
      });
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        setDetailError(detail.detail ?? `${label}失败 (${r.status})`);
        return;
      }
      await refreshEmployees();
      await refreshDepartments();
      // Stay on the detail panel so the operator sees the new
      // status + the inverse button label (the row's
      // separated_at flipped, the panel re-reads from
      // viewingEmp on the next render).
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSavingDetail(false);
    }
  }

  // -- render -------------------------------------------------------------

  const viewingEmp =
    viewingId !== null
      ? (employeeList?.items ?? []).find((e) => e.id === viewingId) ?? null
      : null;

  const employees = employeeList?.items ?? null;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-ink">员工管理</h2>
          <p className="mt-1 text-sm text-ink-soft">
            左侧选部门看该部门下的员工；右侧可加员工、点
            「查看详情」配置 provider 与 API key。
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={openAdd}
            disabled={addingNew}
            className="btn btn-primary px-4 py-2"
          >
            + Add employee
          </button>
        </div>
      </div>

      <div className="glass-card overflow-hidden">
        <div className="flex min-h-[420px]">
          {/* Left: scope picker — "未指定部门" + every department */}
          <nav
            className="w-56 shrink-0 bg-sky-pale/70 backdrop-blur-md border-r border-sky-light/40 p-3"
            aria-label={t("sidebar.orgScopeNavAria")}
          >
            <p className="px-3 mb-1 text-[11px] font-semibold uppercase tracking-wider text-ocean/70">
              范围
            </p>
            <ul className="space-y-0.5">
              <li>
                <button
                  type="button"
                  onClick={() => selectScope({ kind: "unassigned" })}
                  className={
                    "w-full flex items-center justify-between gap-3 px-3 py-2 rounded-md text-sm transition " +
                    (scope.kind === "unassigned"
                      ? "bg-sky-deep text-white shadow-sm"
                      : "text-ocean hover:bg-sky-light/60 hover:text-sky-deep")
                  }
                  aria-current={scope.kind === "unassigned" ? "page" : undefined}
                >
                  <span className="font-medium">未指定部门</span>
                  {unassignedCount() >= 0 && (
                    <span className="text-xs text-ink-soft">
                      {unassignedCount()}
                    </span>
                  )}
                </button>
              </li>

              {departments === null && (
                <li className="px-3 py-2 text-xs text-ink-soft">Loading…</li>
              )}
              {departments?.length === 0 && (
                <li className="px-3 py-2 text-xs text-ink-soft">
                  （还没有部门）
                </li>
              )}
              {departments?.map((d) => {
                const active =
                  scope.kind === "department" && scope.departmentId === d.id;
                const count = deptHeadcount(d.id);
                return (
                  <li key={d.id}>
                    <button
                      type="button"
                      onClick={() =>
                        selectScope({
                          kind: "department",
                          departmentId: d.id,
                        })
                      }
                      className={
                        "w-full flex items-center justify-between gap-3 px-3 py-2 rounded-md text-sm transition " +
                        (active
                          ? "bg-sky-deep text-white shadow-sm"
                          : "text-ocean hover:bg-sky-light/60 hover:text-sky-deep")
                      }
                      aria-current={active ? "page" : undefined}
                    >
                      <span className="font-medium truncate">{d.name}</span>
                      {count >= 0 && (
                        <span className="text-xs text-ink-soft shrink-0">
                          {count}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}

              {/* 已离职员工 scope — sits below the regular dept
                  list and surfaces every employee that's been
                  marked separated, regardless of their last dept.
                  Counts only resolve when this is the active
                  scope (server returns the real total). */}
              <li className="pt-2">
                <button
                  type="button"
                  onClick={() => selectScope({ kind: "separated" })}
                  className={
                    "w-full flex items-center justify-between gap-3 px-3 py-2 rounded-md text-sm transition " +
                    (scope.kind === "separated"
                      ? "bg-sky-deep text-white shadow-sm"
                      : "text-ocean hover:bg-sky-light/60 hover:text-sky-deep")
                  }
                  aria-current={scope.kind === "separated" ? "page" : undefined}
                >
                  <span className="font-medium">已离职员工</span>
                  {separatedCount() >= 0 && (
                    <span className="text-xs text-ink-soft">
                      {separatedCount()}
                    </span>
                  )}
                </button>
              </li>
            </ul>
          </nav>

          {/* Right: employees + add form + detail panel */}
          <div className="flex-1 p-6 space-y-4">
            {loadError && (
              <p className="text-sm text-rose-700">✗ {loadError}</p>
            )}

            {addingNew && (
              <ConsoleCard title="新建员工">
                <div className="space-y-3">
                  <div>
                    <label htmlFor="emp-name" className="form-label">
                      姓名
                    </label>
                    <input
                      id="emp-name"
                      type="text"
                      value={addForm.name}
                      onChange={(e) =>
                        setAddForm((f) => ({ ...f, name: e.target.value }))
                      }
                      placeholder="例如：张三"
                      className="form-input text-sm py-2 px-3"
                    />
                  </div>
                  <div>
                    <label htmlFor="emp-display" className="form-label">
                      显示名（可选）
                    </label>
                    <input
                      id="emp-display"
                      type="text"
                      value={addForm.display_name}
                      onChange={(e) =>
                        setAddForm((f) => ({
                          ...f,
                          display_name: e.target.value,
                        }))
                      }
                      placeholder="留空就用姓名"
                      className="form-input text-sm py-2 px-3"
                    />
                  </div>
                  <div>
                    <label htmlFor="emp-dept" className="form-label">
                      部门
                    </label>
                    <select
                      id="emp-dept"
                      value={addForm.department_id ?? ""}
                      onChange={(e) =>
                        setAddForm((f) => ({
                          ...f,
                          department_id:
                            e.target.value === ""
                              ? null
                              : Number(e.target.value),
                        }))
                      }
                      className="form-input text-sm py-2 px-3"
                    >
                      <option value="">（未指定部门）</option>
                      {(departments ?? []).map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  {addError && (
                    <p className="form-error">✗ {addError}</p>
                  )}
                  <div className="flex items-center gap-2 pt-1">
                    <button
                      type="button"
                      onClick={submitAdd}
                      disabled={adding}
                      className="btn btn-primary text-sm py-2 px-4"
                    >
                      {adding ? "保存中…" : "保存"}
                    </button>
                    <button
                      type="button"
                      onClick={closeAdd}
                      disabled={adding}
                      className="btn btn-ghost text-sm py-2 px-4"
                    >
                      取消
                    </button>
                  </div>
                </div>
              </ConsoleCard>
            )}

            <ConsoleCard title="">
              {/* Toolbar — only on the non-separated scopes, where
                  the toggle makes sense. The dedicated 已离职员工
                  scope is always-separated so the toggle would
                  be a no-op. Count badge reflects the server's
                  total for this scope (page size aside). */}
              <div className="mb-3 flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  {scope.kind !== "separated" && (
                    <label className="flex items-center gap-1.5 text-xs text-ink-soft cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={includeSeparated}
                        onChange={(e) =>
                          toggleIncludeSeparated(e.target.checked)
                        }
                        className="accent-sky-deep"
                      />
                      显示离职员工
                    </label>
                  )}
                </div>
                {employeeList && (
                  <span className="text-xs text-ink-soft">
                    共 {employeeList.total} 人
                    {employeeList.total_pages > 1 &&
                      ` · 第 ${employeeList.page} / ${employeeList.total_pages} 页`}
                  </span>
                )}
              </div>

              {employees === null && !loadError && (
                <p className="text-sm text-ink-soft">Loading…</p>
              )}
              {employees !== null && employees.length === 0 && (
                <p className="form-empty">
                  {scope.kind === "separated"
                    ? "没有已离职员工。"
                    : scope.kind === "unassigned"
                      ? "没有未指定部门的员工。"
                      : "这个部门下还没有员工。"}
                </p>
              )}
              {employees !== null && employees.length > 0 && (
                <table className="data-table w-full">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                      <th className="py-2 pr-4 font-medium">姓名</th>
                      <th className="py-2 pr-4 font-medium">显示名</th>
                      <th className="py-2 pr-4 font-medium">Provider</th>
                      <th className="py-2 font-medium w-24 text-right">
                        操作
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {employees.map((e) => (
                      <tr
                        key={e.id}
                        className={
                          "border-b border-sky-light/30 last:border-0 " +
                          (viewingId === e.id ? "bg-sky-50/50" : "")
                        }
                      >
                        <td className="py-2 pr-4 text-ink font-medium">
                          <span className="inline-flex items-center gap-2">
                            {e.name}
                            {e.separated_at && (
                              <span className="status-pill status-pill--disconnected">
                                已离职
                              </span>
                            )}
                          </span>
                        </td>
                        <td className="py-2 pr-4 text-ink-soft">
                          {e.display_name || (
                            <span className="text-ink-soft">—</span>
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          {e.provider ? (
                            <span className="text-xs font-mono text-ocean">
                              {e.provider}
                            </span>
                          ) : (
                            <span className="text-ink-soft">—</span>
                          )}
                        </td>
                        <td className="py-2 text-right">
                          <button
                            type="button"
                            onClick={() => openDetail(e)}
                            className="text-xs text-sky-700 hover:text-sky-deep transition"
                          >
                            查看详情
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {/* Pagination — prev / page-info / next. Server
                  clamps page to [1, total_pages]; we mirror
                  that on the client so prev/next grey out at
                  the edges. Hidden on a single page so it
                  doesn't add noise when there's nothing to
                  page through. */}
              {employeeList && employeeList.total_pages > 1 && (
                <div className="mt-4 flex items-center justify-end gap-2 text-xs text-ink-soft">
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="btn btn-secondary text-xs py-1 px-2"
                  >
                    ‹ 上一页
                  </button>
                  <span>
                    {employeeList.page} / {employeeList.total_pages}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setPage((p) =>
                        Math.min(employeeList.total_pages, p + 1),
                      )
                    }
                    disabled={page >= employeeList.total_pages}
                    className="btn btn-secondary text-xs py-1 px-2"
                  >
                    下一页 ›
                  </button>
                </div>
              )}
            </ConsoleCard>

            {viewingId !== null && viewingEmp && (
              <ConsoleCard
                title={`员工详情：${viewingEmp.name}`}
              >
                <div className="space-y-3">
                  {viewingEmp.separated_at && (
                    <div className="rounded-md border border-sky-light/40 bg-sky-pale/40 px-3 py-2 text-xs text-ink-soft">
                      已离职
                      {viewingEmp.separated_at && (
                        <>
                          {" — "}
                          <span className="font-mono text-ink">
                            {new Date(viewingEmp.separated_at).toLocaleString()}
                          </span>
                        </>
                      )}
                    </div>
                  )}
                  {/* D.15 — per-employee token usage. Three
                      periods (week / month / total) in one
                      fetch. Numbers are read-only stats; the
                      provider / API key / role form below
                      stays the editing surface. */}
                  <div className="rounded-md border border-sky-light/40 bg-white/40 px-3 py-2 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-ink-soft text-xs">Token 用量</span>
                      {tokenUsage && (
                        <span className="text-xs text-ink-soft font-mono">
                          时区 {tokenUsage.timezone}
                        </span>
                      )}
                    </div>
                    {tokenUsageError && (
                      <p className="form-error mt-1">✗ {tokenUsageError}</p>
                    )}
                    {!tokenUsage && !tokenUsageError && (
                      <p className="mt-1 text-xs text-ink-soft">Loading…</p>
                    )}
                    {tokenUsage && (
                      <div className="mt-1 space-y-0.5 font-mono text-xs">
                        <p>
                          <span className="text-ink-soft">本周</span>{" "}
                          <span className="text-ink">
                            {tokenUsage.week.input_tokens.toLocaleString()} 输入
                          </span>{" "}
                          <span className="text-ink-soft">/</span>{" "}
                          <span className="text-ink">
                            {tokenUsage.week.output_tokens.toLocaleString()} 输出
                          </span>{" "}
                          <span className="text-ink-soft">
                            · {tokenUsage.week.call_count} 次调用
                          </span>
                        </p>
                        <p>
                          <span className="text-ink-soft">本月</span>{" "}
                          <span className="text-ink">
                            {tokenUsage.month.input_tokens.toLocaleString()} 输入
                          </span>{" "}
                          <span className="text-ink-soft">/</span>{" "}
                          <span className="text-ink">
                            {tokenUsage.month.output_tokens.toLocaleString()} 输出
                          </span>{" "}
                          <span className="text-ink-soft">
                            · {tokenUsage.month.call_count} 次调用
                          </span>
                        </p>
                        <p>
                          <span className="text-ink-soft">总计</span>{" "}
                          <span className="text-ink">
                            {tokenUsage.total.input_tokens.toLocaleString()} 输入
                          </span>{" "}
                          <span className="text-ink-soft">/</span>{" "}
                          <span className="text-ink">
                            {tokenUsage.total.output_tokens.toLocaleString()} 输出
                          </span>{" "}
                          <span className="text-ink-soft">
                            · {tokenUsage.total.call_count} 次调用
                          </span>
                        </p>
                      </div>
                    )}
                  </div>
                  <div>
                    <label className="form-label">角色</label>
                    <select
                      value={detailForm.role}
                      onChange={(e) =>
                        setDetailForm((f) => ({
                          ...f,
                          role: e.target.value as
                            | "admin"
                            | "assigned"
                            | "employee"
                            | "guest",
                        }))
                      }
                      className="form-input text-sm py-2 px-3"
                    >
                      <option value="admin">admin（可登录 WebUI）</option>
                      <option value="assigned">
                        assigned（被此 MAGI 服务，走 agent）
                      </option>
                      <option value="employee">
                        employee（其他公司员工，暂不服务）
                      </option>
                      <option value="guest">
                        guest（访客，暂不服务）
                      </option>
                    </select>
                    <p className="mt-1 text-xs text-ink-soft">
                      v0 下 admin 可登录控制台；assigned 走 agent；
                      employee / guest 是多 MAGI / 公开访客的预占值。
                    </p>
                  </div>
                  <div>
                    <label className="form-label">显示名</label>
                    <input
                      type="text"
                      value={detailForm.display_name}
                      onChange={(e) =>
                        setDetailForm((f) => ({
                          ...f,
                          display_name: e.target.value,
                        }))
                      }
                      placeholder="留空就用姓名"
                      className="form-input text-sm py-2 px-3"
                    />
                  </div>
                  <div>
                    <label className="form-label">部门</label>
                    <select
                      value={detailForm.department_id ?? ""}
                      onChange={(e) =>
                        setDetailForm((f) => ({
                          ...f,
                          department_id:
                            e.target.value === ""
                              ? null
                              : Number(e.target.value),
                        }))
                      }
                      className="form-input text-sm py-2 px-3"
                    >
                      <option value="">（未指定部门）</option>
                      {(departments ?? []).map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="form-label">Provider</label>
                    <select
                      value={detailForm.provider}
                      onChange={(e) =>
                        setDetailForm((f) => ({
                          ...f,
                          provider: e.target.value,
                        }))
                      }
                      className="form-input text-sm py-2 px-3"
                    >
                      {PROVIDER_OPTIONS.map((p) => (
                        <option key={p.value} value={p.value}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="form-label">
                      Telegram tgid
                      {detailForm.telegram_id && (
                        <span className="ml-2 text-xs font-normal text-ink-soft">
                          （已绑定 — 留空表示不变，要解绑就清空）
                        </span>
                      )}
                    </label>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={detailForm.telegram_id}
                      onChange={(e) =>
                        setDetailForm((f) => ({
                          ...f,
                          telegram_id: e.target.value,
                        }))
                      }
                      placeholder="例如：123456789（留空 = 解绑）"
                      className="form-input text-sm py-2 px-3 font-mono"
                    />
                  </div>
                  <div>
                    <label className="form-label">
                      API Key
                      {viewingEmp.api_key_set && (
                        <span className="ml-2 text-xs font-normal text-ink-soft">
                          已设置
                        </span>
                      )}
                    </label>
                    <input
                      type="password"
                      value={detailForm.api_key}
                      onChange={(e) =>
                        setDetailForm((f) => ({
                          ...f,
                          api_key: e.target.value,
                        }))
                      }
                      // When a key already exists, show its last-4
                      // as the placeholder so the operator can
                      // visually confirm "this is the one I want
                      // to keep". Typing anything overwrites;
                      // saving with empty string is the no-op
                      // (PATCH skips the field entirely when
                      // api_key is "" in the form).
                      placeholder={
                        viewingEmp.api_key_set && viewingEmp.api_key_last4
                          ? `sk-…${viewingEmp.api_key_last4}`
                          : "sk-..."
                      }
                      autoComplete="new-password"
                      className="form-input text-sm py-2 px-3 font-mono"
                    />
                  </div>

                  {detailError && (
                    <p className="form-error">✗ {detailError}</p>
                  )}

                  <div className="flex items-center gap-2 pt-1">
                    <button
                      type="button"
                      onClick={submitDetail}
                      disabled={savingDetail}
                      className="btn btn-primary text-sm py-2 px-4"
                    >
                      {savingDetail ? "保存中…" : "保存"}
                    </button>
                    <button
                      type="button"
                      onClick={toggleSeparated}
                      disabled={savingDetail}
                      className={
                        viewingEmp.separated_at
                          ? "btn btn-secondary text-sm py-2 px-4"
                          : "btn btn-danger text-sm py-2 px-4"
                      }
                    >
                      {viewingEmp.separated_at ? "恢复在职" : "标记为离职"}
                    </button>
                    <button
                      type="button"
                      onClick={closeDetail}
                      disabled={savingDetail}
                      className="btn btn-ghost text-sm py-2 px-4"
                    >
                      关闭
                    </button>
                  </div>
                </div>
              </ConsoleCard>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
