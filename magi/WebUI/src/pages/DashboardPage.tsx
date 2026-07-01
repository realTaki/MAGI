/**
 * Admin console — Adam's web UI.
 *
 * Adam is the enterprise control plane: HR / IT / admins sign in
 * here, manage employees / EVEs / skills / settings, watch the
 * audit log. EVE is the per-employee agent node; it has its own
 * runtime and its own (much simpler) dashboard — only Chat and a
 * personal Knowledge view, no Admin tab, no Settings tab.
 *
 * C0 ships only Adam (deploy/docker-compose.yml has no eve
 * service yet), so the EVE-specific dashboard is a C6 deliverable.
 * For now the role distinction is documented in this header; the
 * frontend doesn't yet gate tabs by node role because the only
 * node is Adam. When EVE containers come online, the cleanest
 * split is:
 *   - this file stays as `AdamDashboardPage.tsx` (rename at C6)
 *   - a new `EveDashboardPage.tsx` renders just Chat + a scoped
 *     Knowledge (the EVE's *own* personal knowledge, not the
 *     enterprise one)
 *   - `App.tsx` picks which one to mount based on
 *     `GET /api/meta/node-role` (added at C6)
 *
 * Sign-out sits in the header, reached only after a successful
 * sign-in; the boot routing sets `signedInUser` as part of the
 * /me branch, so this should never render the half-state
 * "no one is signed in" path.
 *
 * Each tab owns its own data fetching — the only thing the page
 * bubbles up to App is the bot + admin list (so the rest of the
 * app, e.g. login dropdowns on a future re-sign-in, stays fresh).
 */
import { useEffect, useState } from "react";

import ConsoleCard from "../components/ConsoleCard";
import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import {
  IconActionItems,
  IconConnectors,
  IconContacts,
  IconDailyReports,
  IconDepartments,
  IconEmail,
  IconEmployees,
  IconMeetings,
  IconPlus,
  IconReminders,
  IconScheduledTasks,
  IconSearch,
  IconSkills,
} from "../components/icons";
import type { OnboardingData } from "./onboardingTypes";

export default function DashboardPage(props: {
  data: OnboardingData | null;
  signedInUser: { chat_id: string; display_name: string | null } | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
  onRestart: () => void;
  onSignOut: () => void;
}) {
  // The dashboard is only meaningful after a successful sign-in.
  // The boot routing sets signedInUser as part of the /me branch,
  // so this should never be null in practice — the fallback
  // returns nothing rather than render a confusing half-state.
  if (!props.signedInUser) {
    return null;
  }
  const user = props.signedInUser;
  return (
    <PostLoginLayout
      user={user}
      onSignOut={props.onSignOut}
      data={props.data}
      onBotUpdated={props.onBotUpdated}
      onAdminsChanged={props.onAdminsChanged}
      onRestart={props.onRestart}
    />
  );
}

// Single-row top bar (logo · tabs · signed-in-as · sign-out) plus
// the tab content below. Designed to feel like a slim SaaS nav
// rather than a tall hero card; matches the kind of top bar
// shown in the reference (logo + inline nav + identity pill +
// utility buttons on the right, all on one row).
function PostLoginLayout(props: {
  user: { chat_id: string; display_name: string | null };
  data: OnboardingData | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
  onRestart: () => void;
  onSignOut: () => void;
}) {
  const [tab, setTab] = useState<TabKey>("organization");

  return (
    <main className="min-h-screen flex flex-col">
      <header className="border-b border-slate-200 bg-white/70 backdrop-blur-md">
        <div className="max-w-6xl mx-auto px-6 h-12 flex items-center gap-6">
          <div className="flex items-center gap-2 shrink-0">
            <img
              src="/assets/favicon.svg"
              alt="MAGI"
              width={22}
              height={22}
              className="rounded"
            />
            <span className="text-sm font-semibold tracking-wide text-slate-800">
              MAGI
            </span>
          </div>

          {/* Center the tabs in the available width between the
              logo block and the identity block. flex-1 + justify-center
              lets the logo + identity stay at their natural widths
              (don't shrink) while the tabs sit dead-center. */}
          <div className="flex-1 flex justify-center">
            <InlineTabBar current={tab} onChange={setTab} />
          </div>

          <div className="flex items-center gap-3 shrink-0">
            <span className="text-xs text-slate-500 hidden sm:inline">
              Signed in as{" "}
              <span className="font-mono text-slate-700">
                {props.user.display_name ?? props.user.chat_id}
              </span>
            </span>
            <button
              type="button"
              onClick={props.onSignOut}
              className="rounded-md border border-slate-300 bg-white text-slate-700 px-3 py-1 text-xs font-medium hover:bg-slate-50 transition"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <div className="flex-1 max-w-6xl w-full mx-auto px-6 py-6">
        <div className="space-y-4">
          {tab === "chat" && <ChatTab />}
          {tab === "organization" && <OrganizationTab />}
          {tab === "knowledge" && <KnowledgeTab />}
          {tab === "settings" && (
            <SettingsTab
              data={props.data}
              signedInUser={props.user}
              onBotUpdated={props.onBotUpdated}
              onAdminsChanged={props.onAdminsChanged}
              onRestart={props.onRestart}
            />
          )}
        </div>
      </div>
    </main>
  );
}

// Inline variant of <TabBar> used inside the slim header. No
// rounded card wrapper, no bottom border (the header itself has
// one), no extra padding — tabs are just buttons separated by
// spaces.
function InlineTabBar(props: {
  current: TabKey;
  onChange: (t: TabKey) => void;
}) {
  const tabs: Array<{ key: TabKey; label: string }> = [
    { key: "chat", label: "Chat" },
    { key: "organization", label: "组织" },
    { key: "knowledge", label: "Knowledge" },
    { key: "settings", label: "Settings" },
  ];
  return (
    <nav className="flex items-center gap-1" aria-label="Dashboard sections">
      {tabs.map((t) => {
        const active = t.key === props.current;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => props.onChange(t.key)}
            className={
              "px-3 py-1.5 text-sm font-medium transition rounded-md " +
              (active
                ? "text-sky-700 bg-sky-50"
                : "text-slate-500 hover:text-slate-800 hover:bg-slate-100")
            }
            aria-current={active ? "page" : undefined}
          >
            {t.label}
          </button>
        );
      })}
    </nav>
  );
}

type TabKey = "chat" | "organization" | "knowledge" | "settings";

// -- tab: chat --------------------------------------------------------------
//
// Two-column shell: a sidebar on the left (6 EVE-output categories
// at the top, then a separator, then 新对话 / 搜索对话 / 历史对话
// with a top-20 list and a 查看全部 affordance), and a content
// pane on the right that changes based on what's selected.
//
// C3 wires the TG channel up, C7 fills each section with real
// data. For C0 every section just renders a placeholder pointing
// at the checkpoint that will populate it.
//
// The shell + nav-row visuals come from <SidebarShell> /
// <SidebarNavItem> in components/; the only Chat-specific bits
// are the "belowItems" slot (the separator + actions + history
// list stack on top of the standard nav column) and the per-item
// `pane` field that drives the right-side placeholder.
type ChatItem = SidebarItem & {
  pane: { title: string; hint: string; meta?: string };
};

const CHAT_CATEGORIES: ChatItem[] = [
  {
    id: "action-items",
    label: "Action Items",
    icon: <IconActionItems />,
    pane: {
      title: "Action Items",
      hint: "No action items yet. EVEs will surface follow-ups here once C4 + C5 land.",
      meta: "C4 / C5",
    },
  },
  {
    id: "meetings",
    label: "Meetings",
    icon: <IconMeetings />,
    pane: {
      title: "Meetings",
      hint: "No meetings scheduled. EVEs will book and surface meetings here once C4 lands.",
      meta: "C4",
    },
  },
  {
    id: "reminders",
    label: "Reminders",
    icon: <IconReminders />,
    pane: {
      title: "Reminders",
      hint: "No reminders. EVEs will deliver them here (and on TG) once C5 lands.",
      meta: "C5",
    },
  },
  {
    id: "email",
    label: "Email",
    icon: <IconEmail />,
    pane: {
      title: "Email",
      hint: "No email. The mail channel isn't wired yet — Phase 2.",
      meta: "Phase 2",
    },
  },
  {
    id: "scheduled-tasks",
    label: "Scheduled Tasks",
    icon: <IconScheduledTasks />,
    pane: {
      title: "Scheduled Tasks",
      hint: "No scheduled tasks. EVEs will queue and report on them once C5 lands.",
      meta: "C5",
    },
  },
  {
    id: "daily-reports",
    label: "Daily Reports",
    icon: <IconDailyReports />,
    pane: {
      title: "Daily Reports",
      hint: "No daily reports yet. The proactive engine will generate them once C5 lands.",
      meta: "C5",
    },
  },
];

const CHAT_ACTIONS: ChatItem[] = [
  {
    id: "new-chat",
    label: "新对话",
    icon: <IconPlus />,
    pane: {
      title: "新对话",
      hint: "Pick an employee and start a fresh conversation. C3 wires the TG channel up first; this entry point becomes useful once at least one EVE is dispatched (C6).",
      meta: "C3 / C6",
    },
  },
  {
    id: "search",
    label: "搜索对话",
    icon: <IconSearch />,
    pane: {
      title: "搜索对话",
      hint: "Full-text search across every conversation with an EVE. The index lives in EVE's local SQLite (sqlite-vec) and the result is a deep link into the matching thread.",
      meta: "C3",
    },
  },
];

// Empty for C0 — populated from the audit/event stream once C3
// + C7 land. The shell is the same either way.
const HISTORY: ChatItem[] = [];

/** Cap the visible history list at 20 — beyond that, the "查看全部"
 *  row is the affordance to widen the window. */
const HISTORY_VISIBLE_LIMIT = 20;

function ChatTab() {
  // "view-all" is a synthetic id that aliases the search view (per
  // the design — clicking the last row in the history list should
  // behave like opening search).
  const [selectedId, setSelectedId] = useState<string>(CHAT_CATEGORIES[0].id);

  const allById: Record<string, ChatItem> = {};
  for (const c of CHAT_CATEGORIES) allById[c.id] = c;
  for (const a of CHAT_ACTIONS) allById[a.id] = a;
  for (const h of HISTORY) allById[h.id] = h;
  // view-all is treated as "search" for the right pane.
  allById["view-all"] = allById["search"];

  const selected = allById[selectedId] ?? CHAT_CATEGORIES[0];
  const historyVisible = HISTORY.slice(0, HISTORY_VISIBLE_LIMIT);

  return (
    <SidebarShell
      items={[...CHAT_CATEGORIES, ...CHAT_ACTIONS]}
      selectedId={selectedId}
      onSelect={setSelectedId}
      ariaLabel="Chat navigation"
      belowItems={
        <>
          <hr className="my-3 border-slate-700" />
          <p className="mt-1 mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            历史对话
          </p>
          {historyVisible.length === 0 ? (
            <p className="px-3 text-xs text-slate-500">
              No conversations yet.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {historyVisible.map((h) => (
                <li key={h.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(h.id)}
                    className={
                      "w-full text-left px-3 py-1.5 rounded-md text-xs truncate transition " +
                      (h.id === selectedId
                        ? "bg-slate-700 text-white"
                        : "text-slate-300 hover:bg-slate-800 hover:text-white")
                    }
                    title={h.label}
                  >
                    {h.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            onClick={() => setSelectedId("view-all")}
            className={
              "mt-1 w-full text-left px-3 py-1.5 rounded-md text-xs transition " +
              (selectedId === "view-all"
                ? "bg-slate-700 text-white"
                : "text-sky-300 hover:text-sky-200 hover:bg-slate-800")
            }
          >
            查看全部 →
          </button>
        </>
      }
    >
      <div className="p-8 text-center flex flex-col items-center justify-center">
        <h2 className="text-lg font-semibold text-slate-800">{selected.pane.title}</h2>
        <p className="mt-2 text-sm text-slate-500 max-w-md">{selected.pane.hint}</p>
        {selected.pane.meta && (
          <p className="mt-3 text-xs text-slate-400">{selected.pane.meta}</p>
        )}
      </div>
    </SidebarShell>
  );
}

// -- tab: admin -------------------------------------------------------------
//
// The "contacts" the deployer can reach here are the super admins
// (the chat_ids that may sign in to Adam). The list is fetched
// from /api/auth/allowed-chat-ids because that endpoint already
// resolves display names via Telegram ``getChat`` — saves us a
// second round-trip per row. Adding a new admin runs the same
// code-based flow the wizard used; removing one writes the
// filtered list back via /save-admin.
//
// Edge cases:
//   - The signed-in user can't remove themselves (the X is hidden)
//     so they can't lock themselves out — a coworker admin can
//     still drop them, but you'd have to be a coworker to do that.
//   - The "Add admin" form collapses to a single row by default;
//     it's not a "batch invite" form like the wizard's step 3.
// -- tab: organization ------------------------------------------------------
//
// 组织 (Organization) is Adam-only — EVE doesn't see this tab.
// It owns the people / org structure: departments and the
// employees inside them. The super-admin chat_id list (the
// "who can sign in to Adam" concern) lives in the Settings tab
// instead, since admin access is a system concern, not an org
// concern.
//
// Two sidebar sections:
//   - 部门管理 (Departments) — list of departments, create
//     department, assign manager, add/remove employees
//   - 员工管理 (Employees)   — flat list of every employee, add
//     to a department on creation
//
// C0 ships only the shell; both panes are placeholders pointing
// at C1.1 (ORM + directory CRUD). The data model in the plan:
//   - employees  (id, name, email, telegram_id?, status, ...)
//   - directory  (id, employee_id, display_name, dept, role, ...)
//   - 负责人 = the employee whose directory.role == "lead" within
//     a given dept (or a separate manager_id field — TBD at C1.1)
type OrgSection = "departments" | "employees";

const ORG_SECTIONS: SidebarItem[] = [
  { id: "departments", label: "部门管理", icon: <IconDepartments /> },
  { id: "employees", label: "员工管理", icon: <IconEmployees /> },
];

function OrganizationTab() {
  const [section, setSection] = useState<OrgSection>("departments");

  return (
    <SidebarShell
      items={ORG_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as OrgSection)}
      ariaLabel="Organization sections"
    >
      {section === "departments" && <DepartmentsPane />}
      {section === "employees" && <EmployeesPane />}
    </SidebarShell>
  );
}

// -- pane: 部门管理 ---------------------------------------------------------
//
// CRUD for departments. Columns per the design:
//   部门名称 | 部门人数 | 负责人 | 操作
// C1.1 lands the backend (employees + directory tables).
// -- pane: 部门管理 ---------------------------------------------------------
//
// C1.1 + C1.2: real CRUD against /api/departments + /api/employees.
// The backend returns departments as a flat list with parent_id;
// the frontend builds a parent → children map and DFS-renders
// so the tree structure is visible in the table. Create / edit
// uses a single shared form (collapsed by default), so switching
// between "new" and "edit <id>" is just a state change.
type DepartmentRow = {
  id: number;
  name: string;
  parent_id: number | null;
  manager: { id: number; name: string; display_name: string | null } | null;
  child_count: number;
  created_at: string;
  updated_at: string;
};

type EmployeeRow = {
  id: number;
  name: string;
  display_name: string | null;
  department_id: number | null;
  provider: string | null;
  api_key_set: boolean;
  api_key_last4: string | null;
};

// Master-detail "scope" — what the right pane is showing. The
// "unassigned" item is special: ``null`` (no department) in
// the API's ``?department_id=null`` sense, surfaced as a real
// row in the sidebar so the operator has one click to it.
type EmployeeScope =
  | { kind: "unassigned" }
  | { kind: "department"; departmentId: number };

const PROVIDER_OPTIONS = [
  { value: "", label: "（未指定）" },
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google (Gemini)" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "ollama", label: "Ollama (local)" },
] as const;

// Build a DFS-ordered list of departments with each row's depth,
// so the renderer can indent by depth. The backend's
// ``child_count`` is the number of direct sub-departments; we
// use it both for display and to disable Delete on non-leaves
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

function flattenTree(roots: FlatDept[], out: FlatDept[] = []): FlatDept[] {
  for (const n of roots) {
    out.push(n);
    if (n.children.length) flattenTree(n.children, out);
  }
  return out;
}

function DepartmentsPane() {
  const [departments, setDepartments] = useState<DepartmentRow[] | null>(null);
  const [employees, setEmployees] = useState<EmployeeRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

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
  // Total members of a department: it's the employee count for
  // that department (from the cached employees list). For
  // non-leaf depts this is just the dept's own members, not
  // the recursive subtree — recursive count would require a
  // second endpoint or client-side walk. Used by the inline
  // sub-dept disable / enable logic in the edit form.
  function memberCount(deptId: number): number {
    if (scope.kind !== "department" || scope.departmentId !== deptId) {
      return (employees ?? []).filter((e) => e.department_id === deptId).length;
    }
    return employees?.length ?? 0;
  }
  const tree = departments ? buildTree(departments) : [];
  const flat = flattenTree(tree);

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
          <h2 className="text-lg font-semibold text-slate-800">部门管理</h2>
          <p className="mt-1 text-sm text-slate-600">
            树形组织结构。每个部门可以指定负责人，子部门通过
            「上级部门」字段挂在父节点下。
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={openCreate}
            disabled={formOpen && !addingNew}
            className="rounded-md bg-sky-700 text-white px-4 py-2 text-sm font-medium shadow-sm hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            + Create department
          </button>
        </div>
      </div>

      {formOpen && (
        <ConsoleCard title={addingNew ? "新建部门" : "编辑部门"}>
          <div className="space-y-3">
            <div>
              <label
                htmlFor="dept-name"
                className="block text-sm font-medium text-slate-700 mb-1"
              >
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
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label
                  htmlFor="dept-parent"
                  className="block text-sm font-medium text-slate-700 mb-1"
                >
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
                  className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
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
                <label
                  htmlFor="dept-manager"
                  className="block text-sm font-medium text-slate-700 mb-1"
                >
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
                  className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
                >
                  <option value="">（无）</option>
                  {(employees ?? []).map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.display_name || e.name}
                    </option>
                  ))}
                </select>
                {(employees ?? []).length === 0 && (
                  <p className="mt-1 text-xs text-slate-400">
                    还没有员工。切到「员工管理」先创建。
                  </p>
                )}
              </div>
            </div>

            {formError && (
              <p className="text-sm text-rose-700">✗ {formError}</p>
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
                <div className="flex items-center gap-2 pt-3 border-t border-slate-200 flex-wrap">
                  {editing && (
                    <>
                      <button
                        type="button"
                        onClick={() => openCreateChild(editing.id)}
                        disabled={saving}
                        className="rounded-md bg-emerald-600 text-white px-3 py-1.5 text-sm font-medium hover:bg-emerald-700 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
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
                        className="rounded-md border border-rose-200 bg-white text-rose-700 px-3 py-1.5 text-sm font-medium hover:bg-rose-50 transition disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-white"
                      >
                        删除部门
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    onClick={save}
                    disabled={saving}
                    className={`rounded-md bg-emerald-600 text-white px-4 py-1.5 text-sm font-medium hover:bg-emerald-700 transition disabled:bg-slate-300 disabled:cursor-not-allowed ${editing ? "ml-auto" : ""}`}
                  >
                    {saving ? "保存中…" : "保存"}
                  </button>
                  <button
                    type="button"
                    onClick={closeForm}
                    disabled={saving}
                    className="rounded-md border border-slate-300 bg-white text-slate-700 px-4 py-1.5 text-sm font-medium hover:bg-slate-50 transition disabled:opacity-50"
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
          <p className="text-sm text-rose-700 mb-3">✗ {loadError}</p>
        )}
        {departments === null && !loadError && (
          <p className="text-sm text-slate-500">Loading…</p>
        )}
        {departments !== null && departments.length === 0 && (
          <p className="py-6 text-center text-slate-400 text-sm">
            还没有部门。点 + Create department 开始。
          </p>
        )}
        {departments !== null && departments.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-slate-500 border-b border-slate-200">
                <th className="py-2 pr-4 font-medium">部门名称</th>
                <th className="py-2 pr-4 font-medium w-24">子部门数</th>
                <th className="py-2 pr-4 font-medium">负责人</th>
                <th className="py-2 font-medium w-28 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {flat.map((d) => {
                const isEditing = editingId === d.id;
                return (
                  <tr
                    key={d.id}
                    className={
                      "border-b border-slate-100 last:border-0 " +
                      (isEditing ? "bg-sky-50/50" : "")
                    }
                  >
                    <td className="py-2 pr-4 text-slate-900">
                      <span
                        style={{ paddingLeft: `${d.depth * 20}px` }}
                        className="inline-flex items-center gap-1"
                      >
                        {d.depth > 0 && (
                          <span className="text-slate-300">└</span>
                        )}
                        <span className="font-medium">{d.name}</span>
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-slate-600">
                      {d.child_count}
                    </td>
                    <td className="py-2 pr-4 text-slate-600">
                      {d.manager ? (
                        d.manager.display_name || d.manager.name
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="py-2 text-right space-x-2">
                      <button
                        type="button"
                        onClick={() => openEdit(d)}
                        disabled={formOpen && !isEditing}
                        className="text-xs text-sky-700 hover:text-sky-800 transition disabled:text-slate-300 disabled:cursor-not-allowed"
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

// -- pane: 员工管理 ---------------------------------------------------------
//
// Flat list of every employee in the company, with their dept
// and status. "Add employee" creates a row in `employees`; the
// TG-binding step (C2) writes `telegram_id` into the same row
// once the employee proves ownership of the chat from TG.
// -- pane: 员工管理 ---------------------------------------------------------
//
// C1.1 minimal: list + add. Department assignment + TG binding +
// status land with C1.2 / C2. The columns that need those
// (部门 / TG chat_id / 状态) render "—" until then so the table
// shape stays stable across checkpoints.
//
// Master-detail: left sidebar lists the departments + a
// "未指定部门" pseudo-item; right pane shows the employees
// in the selected scope. Clicking 查看详情 on a row opens
// an inline detail panel for the LLM provider / API key
// configuration.
function EmployeesPane() {
  const [departments, setDepartments] = useState<DepartmentRow[] | null>(null);
  const [employees, setEmployees] = useState<EmployeeRow[] | null>(null);
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
  }>({ display_name: "", department_id: null, provider: "", api_key: "" });
  const [detailError, setDetailError] = useState<string | null>(null);
  const [savingDetail, setSavingDetail] = useState(false);

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
      const qs =
        scope.kind === "unassigned"
          ? "?unassigned=true"
          : `?department_id=${scope.departmentId}`;
      const r = await fetch(`/api/employees${qs}`, { credentials: "include" });
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      setEmployees(await r.json());
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  // Re-fetch on mount + whenever the scope changes. Departments
  // are re-fetched on mount only (the master list doesn't
  // change as the user clicks around).
  useEffect(() => {
    void refreshDepartments();
  }, []);
  useEffect(() => {
    void refreshEmployees();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind === "department" ? scope.departmentId : "unassigned"]);

  // -- helpers ------------------------------------------------------------

  function unassignedCount(): number {
    // The list endpoint only returns one scope at a time, so
    // for the unassigned count we issue a tiny extra fetch.
    // Cheap: it's just a count of the right pane, or —
    // better — we have it when ``scope.kind === 'unassigned'``.
    if (scope.kind === "unassigned") {
      return employees?.length ?? 0;
    }
    // Otherwise we don't know without another fetch; render
    // "—" rather than burn a request per click.
    return -1;
  }

  function deptHeadcount(deptId: number): number {
    if (scope.kind === "department" && scope.departmentId === deptId) {
      return employees?.length ?? 0;
    }
    return -1;
  }

  function selectScope(next: EmployeeScope) {
    setScope(next);
    setViewingId(null); // close the detail panel on scope change
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
    });
    setDetailError(null);
  }

  function closeDetail() {
    setViewingId(null);
    setDetailError(null);
  }

  async function submitDetail() {
    if (viewingId === null) return;
    setSavingDetail(true);
    setDetailError(null);
    try {
      const body: Record<string, unknown> = {
        display_name: detailForm.display_name.trim() || null,
        department_id: detailForm.department_id,
        provider: detailForm.provider || null,
      };
      // Only send api_key when the user actually typed something
      // — empty string would clear the stored key (intentional
      // for rotate, but ``null`` means "don't change" so the
      // default PATCH semantics keep an existing key).
      if (detailForm.api_key !== "") {
        body.api_key = detailForm.api_key;
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

  // -- render -------------------------------------------------------------

  const viewingEmp =
    viewingId !== null
      ? (employees ?? []).find((e) => e.id === viewingId) ?? null
      : null;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">员工管理</h2>
          <p className="mt-1 text-sm text-slate-600">
            左侧选部门看该部门下的员工；右侧可加员工、点
            「查看详情」配置 provider 与 API key。
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={openAdd}
            disabled={addingNew}
            className="rounded-md bg-sky-700 text-white px-4 py-2 text-sm font-medium shadow-sm hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            + Add employee
          </button>
        </div>
      </div>

      <div className="rounded-2xl bg-white/80 backdrop-blur-md shadow-lg shadow-sky-900/5 border border-white/60 overflow-hidden">
        <div className="flex min-h-[420px]">
          {/* Left: scope picker — "未指定部门" + every department */}
          <nav
            className="w-56 shrink-0 bg-slate-900 text-slate-100 p-3"
            aria-label="Employee scope"
          >
            <p className="px-3 mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
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
                      ? "bg-slate-700 text-white"
                      : "text-slate-300 hover:bg-slate-800 hover:text-white")
                  }
                  aria-current={scope.kind === "unassigned" ? "page" : undefined}
                >
                  <span className="font-medium">未指定部门</span>
                  {unassignedCount() >= 0 && (
                    <span className="text-xs text-slate-400">
                      {unassignedCount()}
                    </span>
                  )}
                </button>
              </li>

              {departments === null && (
                <li className="px-3 py-2 text-xs text-slate-500">Loading…</li>
              )}
              {departments?.length === 0 && (
                <li className="px-3 py-2 text-xs text-slate-500">
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
                          ? "bg-slate-700 text-white"
                          : "text-slate-300 hover:bg-slate-800 hover:text-white")
                      }
                      aria-current={active ? "page" : undefined}
                    >
                      <span className="font-medium truncate">{d.name}</span>
                      {count >= 0 && (
                        <span className="text-xs text-slate-400 shrink-0">
                          {count}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
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
                    <label
                      htmlFor="emp-name"
                      className="block text-sm font-medium text-slate-700 mb-1"
                    >
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
                      className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label
                      htmlFor="emp-display"
                      className="block text-sm font-medium text-slate-700 mb-1"
                    >
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
                      className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label
                      htmlFor="emp-dept"
                      className="block text-sm font-medium text-slate-700 mb-1"
                    >
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
                      className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
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
                    <p className="text-sm text-rose-700">✗ {addError}</p>
                  )}
                  <div className="flex items-center gap-2 pt-1">
                    <button
                      type="button"
                      onClick={submitAdd}
                      disabled={adding}
                      className="rounded-md bg-emerald-600 text-white px-4 py-2 text-sm font-medium hover:bg-emerald-700 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
                    >
                      {adding ? "保存中…" : "保存"}
                    </button>
                    <button
                      type="button"
                      onClick={closeAdd}
                      disabled={adding}
                      className="rounded-md border border-slate-300 bg-white text-slate-700 px-4 py-2 text-sm font-medium hover:bg-slate-50 transition disabled:opacity-50"
                    >
                      取消
                    </button>
                  </div>
                </div>
              </ConsoleCard>
            )}

            <ConsoleCard title="">
              {employees === null && !loadError && (
                <p className="text-sm text-slate-500">Loading…</p>
              )}
              {employees !== null && employees.length === 0 && (
                <p className="py-6 text-center text-slate-400 text-sm">
                  {scope.kind === "unassigned"
                    ? "没有未指定部门的员工。"
                    : "这个部门下还没有员工。"}
                </p>
              )}
              {employees !== null && employees.length > 0 && (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wider text-slate-500 border-b border-slate-200">
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
                          "border-b border-slate-100 last:border-0 " +
                          (viewingId === e.id ? "bg-sky-50/50" : "")
                        }
                      >
                        <td className="py-2 pr-4 text-slate-900 font-medium">
                          {e.name}
                        </td>
                        <td className="py-2 pr-4 text-slate-600">
                          {e.display_name || (
                            <span className="text-slate-400">—</span>
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          {e.provider ? (
                            <span className="text-xs font-mono text-slate-700">
                              {e.provider}
                            </span>
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </td>
                        <td className="py-2 text-right">
                          <button
                            type="button"
                            onClick={() => openDetail(e)}
                            className="text-xs text-sky-700 hover:text-sky-800 transition"
                          >
                            查看详情
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </ConsoleCard>

            {viewingId !== null && viewingEmp && (
              <ConsoleCard title={`员工详情：${viewingEmp.name}`}>
                <div className="space-y-3">
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1">
                      显示名
                    </label>
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
                      className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1">
                      部门
                    </label>
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
                      className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
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
                    <label className="block text-sm font-medium text-slate-700 mb-1">
                      Provider
                    </label>
                    <select
                      value={detailForm.provider}
                      onChange={(e) =>
                        setDetailForm((f) => ({
                          ...f,
                          provider: e.target.value,
                        }))
                      }
                      className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
                    >
                      {PROVIDER_OPTIONS.map((p) => (
                        <option key={p.value} value={p.value}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1">
                      API Key
                      {viewingEmp.api_key_set && (
                        <span className="ml-2 text-xs font-normal text-slate-500">
                          已设置（…{viewingEmp.api_key_last4}）— 留空表示不变，要
                          rotate 就填新值
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
                      placeholder={
                        viewingEmp.api_key_set
                          ? "留空保持不变"
                          : "sk-..."
                      }
                      autoComplete="new-password"
                      className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-mono shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
                    />
                  </div>

                  {detailError && (
                    <p className="text-sm text-rose-700">✗ {detailError}</p>
                  )}

                  <div className="flex items-center gap-2 pt-1">
                    <button
                      type="button"
                      onClick={submitDetail}
                      disabled={savingDetail}
                      className="rounded-md bg-emerald-600 text-white px-4 py-2 text-sm font-medium hover:bg-emerald-700 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
                    >
                      {savingDetail ? "保存中…" : "保存"}
                    </button>
                    <button
                      type="button"
                      onClick={closeDetail}
                      disabled={savingDetail}
                      className="rounded-md border border-slate-300 bg-white text-slate-700 px-4 py-2 text-sm font-medium hover:bg-slate-50 transition disabled:opacity-50"
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

type AllowedAccount = {
  chat_id: string;
  display_name: string | null;
  role: string;
};

// Inline add-admin form: chat_id → Send code → 6 digits → Verify.
// Mirrors the wizard's Step 3 row but as a single self-contained
// subcomponent (no add-another-row affordance — if you want
// another, click "+ Add admin" again after this one verifies).
function AddAdminForm(props: {
  onAdded: (chatId: string, displayName: string | null) => void;
  onCancel: () => void;
}) {
  const [chatId, setChatId] = useState("");
  const [code, setCode] = useState("");
  const [state, setState] = useState<
    "idle" | "sending" | "code-sent" | "verifying" | "error"
  >("idle");
  const [error, setError] = useState<string | null>(null);

  async function sendCode() {
    const cid = chatId.trim();
    if (!/^-?\d+$/.test(cid)) {
      setState("error");
      setError("chat_id must be numeric");
      return;
    }
    setState("sending");
    setError(null);
    try {
      const r = await fetch("/api/onboarding/send-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: cid }),
        credentials: "include",
      });
      const data = (await r.json()) as { ok: boolean; error?: string };
      if (data.ok) {
        setState("code-sent");
      } else {
        setState("error");
        setError(data.error ?? "Failed to send code");
      }
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "Network error");
    }
  }

  async function verifyCode() {
    const cid = chatId.trim();
    const c = code.trim();
    if (c.length !== 6) {
      setState("error");
      setError("Code must be 6 digits");
      return;
    }
    setState("verifying");
    setError(null);
    try {
      const r = await fetch("/api/onboarding/verify-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: cid, code: c }),
        credentials: "include",
      });
      const data = (await r.json()) as {
        ok: boolean;
        display_name?: string | null;
        error?: string;
      };
      if (data.ok) {
        // The endpoint already appended the chat_id to settings; we
        // just need to tell the parent to refresh.
        props.onAdded(cid, data.display_name ?? null);
      } else {
        setState("error");
        setError(data.error ?? "Code did not match");
      }
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "Network error");
    }
  }

  const codeInputVisible =
    state === "code-sent" || state === "verifying" || state === "error";

  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-white/60 p-3">
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={chatId}
          onChange={(e) => {
            setChatId(e.target.value);
            if (state === "error") setState("idle");
          }}
          placeholder="TG chat ID"
          className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-mono shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
        />
        <button
          type="button"
          onClick={sendCode}
          disabled={
            state === "sending" ||
            state === "verifying" ||
            !chatId.trim()
          }
          className="rounded-md bg-sky-700 text-white px-3 py-2 text-sm font-medium hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
        >
          {state === "sending"
            ? "Sending…"
            : state === "code-sent"
              ? "Resend"
              : "Send code"}
        </button>
        <button
          type="button"
          onClick={props.onCancel}
          className="rounded-md border border-slate-200 bg-white text-slate-500 px-2 py-2 text-sm hover:bg-slate-50 transition shrink-0"
          title="Cancel"
        >
          ✕
        </button>
      </div>

      {codeInputVisible && (
        <div className="mt-2 flex items-center gap-2">
          <input
            type="text"
            inputMode="numeric"
            maxLength={6}
            value={code}
            onChange={(e) =>
              setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
            }
            placeholder="6-digit code from TG"
            className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-mono tracking-widest shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
            disabled={state === "verifying"}
          />
          <button
            type="button"
            onClick={verifyCode}
            disabled={state === "verifying" || code.length !== 6}
            className="rounded-md bg-sky-700 text-white px-3 py-2 text-sm font-medium hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
          >
            {state === "verifying" ? "Verifying…" : "Verify"}
          </button>
        </div>
      )}

      {state === "error" && error && (
        <p className="mt-2 text-xs text-rose-700">✗ {error}</p>
      )}
      {state === "code-sent" && (
        <p className="mt-2 text-xs text-sky-700">
          Code sent — check the Telegram chat and enter the 6 digits.
        </p>
      )}
    </div>
  );
}

// -- tab: knowledge ---------------------------------------------------------
//
// Three-section left sidebar (Skills / Connectors / Contacts) that
// mirrors the Chat tab's pattern. All three are placeholders for
// C0 — each pane points at the checkpoint that will populate it:
//   - Skills      — C4 (SkillRunner + 4 MVP skills)
//   - Connectors  — Phase 2 (Email / Calendar); Telegram is "live"
//                   in the sense that the wizard configured it, but
//                   the channel abstraction lands in C3
//   - Contacts    — C1.1 (employee directory; for now the only
//                   "contacts" we have are the super admins, which
//                   live in the Settings tab)
type KnowledgeSection = "skills" | "connectors" | "contacts";

const KNOWLEDGE_SECTIONS: SidebarItem[] = [
  { id: "skills", label: "Skills", icon: <IconSkills /> },
  { id: "connectors", label: "Connectors", icon: <IconConnectors /> },
  { id: "contacts", label: "Contacts", icon: <IconContacts /> },
];

function KnowledgeTab() {
  const [section, setSection] = useState<KnowledgeSection>("skills");

  return (
    <SidebarShell
      items={KNOWLEDGE_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as KnowledgeSection)}
      ariaLabel="Knowledge sections"
    >
      {section === "skills" && <KnowledgeSkillsPane />}
      {section === "connectors" && <KnowledgeConnectorsPane />}
      {section === "contacts" && <KnowledgeContactsPane />}
    </SidebarShell>
  );
}

// -- pane: skills -----------------------------------------------------------
//
// The skill registry lives here rather than in the Admin tab
// because skills are *capabilities* the deployer (and EVEs) draw
// on, not operational state. C4 lands the SkillRunner + 4 MVP
// skills and the per-EVE assignment UI.
function KnowledgeSkillsPane() {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-800">Skills</h2>
        <p className="mt-1 text-sm text-slate-600">
          Reusable actions EVEs can call — schedule reminders,
          book meetings, search the knowledge base, collect info.
          The 4 MVP skills land with C4.
        </p>
        <p className="mt-2 text-xs text-slate-400">C4 — Skill runner + 4 MVP skills</p>
      </div>
      <ConsoleCard title="Registry">
        <p className="text-sm text-slate-500">0 skills registered</p>
        <p className="mt-1 text-xs text-slate-400">
          The 4 MVP skills will appear here automatically.
        </p>
      </ConsoleCard>
    </div>
  );
}

// -- pane: connectors --------------------------------------------------------
//
// The channels each EVE talks through. Telegram is live today
// (it's the channel the wizard configured); Email and Calendar
// are Phase 2. "Connectors" is the umbrella term — a connector
// is the inbound/outbound adapter for one platform, and a node
// can mount any subset.
function KnowledgeConnectorsPane() {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-800">Connectors</h2>
        <p className="mt-1 text-sm text-slate-600">
          Channels EVEs talk through. Each connector is one
          platform; nodes mount the subset they need.
        </p>
        <p className="mt-2 text-xs text-slate-400">Phase 2 — Email / Calendar</p>
      </div>
      <div className="space-y-2">
        <KnowledgeConnectorRow
          name="Telegram"
          status="connected"
          note="Wired by the onboarding wizard"
        />
        <KnowledgeConnectorRow
          name="Email"
          status="coming"
          note="Inbound + outbound IMAP/SMTP — Phase 2"
        />
        <KnowledgeConnectorRow
          name="Calendar"
          status="coming"
          note="Google / Microsoft — Phase 2"
        />
      </div>
    </div>
  );
}

function KnowledgeConnectorRow(props: {
  name: string;
  status: "connected" | "coming";
  note: string;
}) {
  const badge =
    props.status === "connected"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : "bg-slate-50 text-slate-500 border-slate-200";
  const label = props.status === "connected" ? "connected" : "coming soon";
  return (
    <div className="rounded-lg border border-slate-200 bg-white/60 p-3 flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-slate-900">{props.name}</div>
        <div className="text-xs text-slate-500">{props.note}</div>
      </div>
      <span
        className={
          "text-xs border rounded px-1.5 py-0.5 shrink-0 " + badge
        }
      >
        {label}
      </span>
    </div>
  );
}

// -- pane: contacts ---------------------------------------------------------
//
// The enterprise directory once C1.1 ships. For C0 we have no
// real employee table — the only "contacts" are the super admins,
// and those live in the Admin tab (since that's where their
// lifecycle belongs). This pane is a placeholder pointing at
// C1.1 so the section is reachable.
function KnowledgeContactsPane() {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-800">Contacts</h2>
        <p className="mt-1 text-sm text-slate-600">
          The company directory — every employee an EVE can reach.
          Scoped per employee; each row carries display name,
          department, role, and contact channels.
        </p>
        <p className="mt-2 text-xs text-slate-400">
          C1.1 — ORM + directory CRUD
        </p>
      </div>
      <ConsoleCard title="Directory">
        <p className="text-sm text-slate-500">0 employees</p>
        <p className="mt-1 text-xs text-slate-400">
          C1.1 fills this in. The super-admin list (a different
          concern) lives in the Admin tab.
        </p>
      </ConsoleCard>
    </div>
  );
}

// -- tab: settings ----------------------------------------------------------
//
// Three things live here:
//   1. Telegram bot token (re-set flow)
//   2. WebUI Access — the chat_ids that may sign in to Adam
//      (super admins + assigned employees; this used to be the
//      "Admin contacts" card on the old Admin tab; it's a
//      system concern, not an org concern)
//   3. Onboarding escape hatch (re-run the wizard)
//
// Per-checkpoint settings (LLM provider keys, audit retention,
// quiet hours) get added here as those checkpoints land.
function SettingsTab(props: {
  data: OnboardingData | null;
  signedInUser: { chat_id: string; display_name: string | null };
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
  onRestart: () => void;
}) {
  return (
    <div className="space-y-4">
      <SettingsChannelsCard
        data={props.data}
        onBotUpdated={props.onBotUpdated}
      />
      <SettingsWebuiAccessCard
        signedInUser={props.signedInUser}
        onAdminsChanged={props.onAdminsChanged}
      />
      <SettingsOnboardingCard onRestart={props.onRestart} />
    </div>
  );
}

// -- Channels card ------------------------------------------------------------
//
// One row per platform adapter the node can mount. WebUI and
// Telegram are the live ones today (WebUI is the console we're
// using right now; Telegram is the IM channel the wizard
// configured). The rest — WeChat, Lark, Teams — are listed as
// "coming soon" so the deployer can see the planned surface
// area. The Telegram row carries the "Re-set" action; the others
// are inert for C0.
//
// "Coming soon" rows are rendered with reduced opacity to
// communicate "not actionable" without taking them out of the
// list. A future Phase 2 / 3 lands Email (IMAP/SMTP), Calendar
// (Google / Microsoft) and the WeChat / Lark / Teams adapters
// — at that point each new row gets its own inline config form
// modelled on the Telegram Re-set token flow.
function SettingsChannelsCard(props: {
  data: OnboardingData | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
}) {
  const [editing, setEditing] = useState(false);

  const tgConnected = !!props.data?.bot.username;
  const tgNote = props.data
    ? `@${props.data.bot.username}` +
      (props.data.bot.token
        ? ` · ${props.data.bot.token.slice(0, 6)}…${props.data.bot.token.slice(-4)}`
        : "")
    : "(not configured)";

  return (
    <ConsoleCard title="Channels">
      <p className="text-sm text-slate-600">
        Platform adapters the node can mount. WebUI is the
        console you're using; Telegram is the IM channel the
        wizard configured. The rest are planned.
      </p>

      <table className="w-full text-sm mt-4">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-slate-500 border-b border-slate-200">
            <th className="py-2 pr-4 font-medium">Name</th>
            <th className="py-2 pr-4 font-medium w-32">Status</th>
            <th className="py-2 pr-4 font-medium">Notes</th>
            <th className="py-2 font-medium w-24 text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-slate-100">
            <td className="py-2 pr-4 text-slate-900">WebUI</td>
            <td className="py-2 pr-4">
              <ChannelStatusBadge status="connected" />
            </td>
            <td className="py-2 pr-4 text-slate-600 font-mono text-xs">
              :42069
            </td>
            <td className="py-2 text-right text-xs text-slate-400">—</td>
          </tr>

          <tr className="border-b border-slate-100">
            <td className="py-2 pr-4 text-slate-900">Telegram</td>
            <td className="py-2 pr-4">
              <ChannelStatusBadge
                status={tgConnected ? "connected" : "disconnected"}
              />
            </td>
            <td className="py-2 pr-4 text-slate-600 font-mono text-xs">
              {tgNote}
            </td>
            <td className="py-2 text-right">
              {tgConnected && !editing && (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="text-sm text-sky-700 hover:text-sky-800 transition"
                >
                  Re-set
                </button>
              )}
            </td>
          </tr>

          <ComingChannelRow name="WeChat" />
          <ComingChannelRow name="Lark" />
          <ComingChannelRow name="Teams" />
        </tbody>
      </table>

      {editing && (
        <div className="mt-4 border-t border-slate-200 pt-4">
          <BotTokenField
            onSaved={(token, username) => {
              props.onBotUpdated({ token, username });
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
          />
        </div>
      )}
    </ConsoleCard>
  );
}

function ComingChannelRow(props: { name: string }) {
  return (
    <tr className="border-b border-slate-100 last:border-0 opacity-50">
      <td className="py-2 pr-4 text-slate-700">{props.name}</td>
      <td className="py-2 pr-4">
        <ChannelStatusBadge status="coming" />
      </td>
      <td className="py-2 pr-4 text-slate-500">—</td>
      <td className="py-2 text-right text-xs text-slate-400">—</td>
    </tr>
  );
}

function ChannelStatusBadge(props: {
  status: "connected" | "disconnected" | "coming";
}) {
  switch (props.status) {
    case "connected":
      return (
        <span className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">
          connected
        </span>
      );
    case "disconnected":
      return (
        <span className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded px-1.5 py-0.5">
          disconnected
        </span>
      );
    case "coming":
      return (
        <span className="text-xs text-slate-500 bg-slate-100 border border-slate-200 rounded px-1.5 py-0.5">
          coming soon
        </span>
      );
  }
}

function SettingsWebuiAccessCard(props: {
  signedInUser: { chat_id: string; display_name: string | null };
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
}) {
  // Local state is the source of truth for what the card shows.
  // On mount we fetch the server view (which has display names);
  // every mutation re-fetches so we don't drift.
  const [accounts, setAccounts] = useState<AllowedAccount[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [addingNew, setAddingNew] = useState(false);

  async function refresh() {
    setLoadError(null);
    try {
      const r = await fetch("/api/auth/allowed-chat-ids", {
        credentials: "include",
      });
      if (!r.ok) {
        setLoadError("Failed to load access list");
        return;
      }
      const data = (await r.json()) as { accounts: AllowedAccount[] };
      setAccounts(data.accounts);
      // Bubble the updated admin list up to App so the rest of the
      // dashboard (header, etc.) stays consistent.
      props.onAdminsChanged(
        data.accounts
          .filter((a) => a.role === "super_admin")
          .map((a) => ({
            chatId: a.chat_id,
            displayName: a.display_name,
          })),
      );
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleRemoveAdmin(chatId: string) {
    if (!accounts) return;
    if (chatId === props.signedInUser.chat_id) return; // belt + suspenders
    // Only super_admin rows are removed via this card; the
    // backend save-admin endpoint just replaces the super_admins
    // list, so the employees side is unaffected.
    const remaining = accounts
      .filter((a) => a.role === "super_admin" && a.chat_id !== chatId)
      .map((a) => a.chat_id);
    const r = await fetch("/api/onboarding/save-admin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_ids: remaining }),
      credentials: "include",
    });
    if (r.ok) {
      await refresh();
    } else {
      setLoadError("Failed to remove admin");
    }
  }

  const superAdmins = accounts?.filter((a) => a.role === "super_admin") ?? [];
  const assignedEmployees =
    accounts?.filter((a) => a.role === "assigned_employee") ?? [];

  return (
    <ConsoleCard title="WebUI Access">
      <p className="text-sm text-slate-600">
        Chat IDs that may sign in to Adam. Two kinds:
        <span className="font-medium"> super admins</span> are
        wizard-configured (deployers) and
        <span className="font-medium"> assigned employees</span>{" "}
        are employees with a dispatched EVE (C6). Adding a
        super admin runs the code-based verify flow; removing
        one writes the filtered list back. Employees are
        managed by the EVE lifecycle, not this card.
      </p>

      <div className="mt-4">
        {accounts === null && !loadError && (
          <p className="text-sm text-slate-500">Loading…</p>
        )}
        {loadError && <p className="text-sm text-rose-700">✗ {loadError}</p>}
        {accounts !== null && accounts.length === 0 && (
          <p className="text-sm text-slate-500">
            No one has access yet. Add a super admin below, or
            dispatch an EVE in the organization tab (C6).
          </p>
        )}
        {accounts !== null && accounts.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-slate-500 border-b border-slate-200">
                <th className="py-2 pr-4 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium w-44">Role</th>
                <th className="py-2 pr-4 font-medium">TG chat_id</th>
                <th className="py-2 font-medium w-28 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {superAdmins.map((a) => {
                const isSelf = a.chat_id === props.signedInUser.chat_id;
                return (
                  <tr
                    key={a.chat_id}
                    className="border-b border-slate-100 last:border-0"
                  >
                    <td className="py-2 pr-4 text-slate-900">
                      {a.display_name ?? (
                        <span className="text-slate-400">(no display name)</span>
                      )}
                    </td>
                    <td className="py-2 pr-4">
                      <RoleBadge role="super_admin" />
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-slate-500">
                      {a.chat_id}
                    </td>
                    <td className="py-2 text-right">
                      {isSelf ? (
                        <span className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">
                          you
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => handleRemoveAdmin(a.chat_id)}
                          title="Remove this super admin"
                          className="rounded-md border border-slate-200 bg-white text-slate-500 px-2 py-1 text-xs hover:bg-slate-50 hover:text-rose-700 transition"
                        >
                          ✕ Remove
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {assignedEmployees.map((a) => (
                <tr
                  key={a.chat_id}
                  className="border-b border-slate-100 last:border-0"
                >
                  <td className="py-2 pr-4 text-slate-900">
                    {a.display_name ?? (
                      <span className="text-slate-400">(no display name)</span>
                    )}
                  </td>
                  <td className="py-2 pr-4">
                    <RoleBadge role="assigned_employee" />
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs text-slate-500">
                    {a.chat_id}
                  </td>
                  <td className="py-2 text-right text-xs text-slate-400">
                    via EVE
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {!addingNew && (
          <button
            type="button"
            onClick={() => setAddingNew(true)}
            className="mt-3 text-sm text-sky-700 hover:text-sky-800 transition"
          >
            + Add super admin
          </button>
        )}

        {addingNew && (
          <AddAdminForm
            onAdded={() => {
              setAddingNew(false);
              void refresh();
            }}
            onCancel={() => setAddingNew(false)}
          />
        )}
      </div>
    </ConsoleCard>
  );
}

function RoleBadge(props: { role: "super_admin" | "assigned_employee" }) {
  if (props.role === "super_admin") {
    return (
      <span className="text-xs text-slate-700 bg-slate-100 border border-slate-200 rounded px-1.5 py-0.5">
        super admin
      </span>
    );
  }
  return (
    <span className="text-xs text-sky-700 bg-sky-50 border border-sky-200 rounded px-1.5 py-0.5">
      assigned employee
    </span>
  );
}

function SettingsOnboardingCard(props: { onRestart: () => void }) {
  return (
    <ConsoleCard title="Onboarding">
      <p className="text-sm text-slate-600">
        Re-run the first-time setup wizard. Saved bot and admin
        rows stay in SQLite; the wizard will resume from wherever
        it left off.
      </p>
      <button
        type="button"
        onClick={props.onRestart}
        className="mt-3 rounded-md border border-slate-300 bg-white text-slate-700 px-4 py-2 text-sm font-medium hover:bg-slate-50 transition"
      >
        Restart onboarding
      </button>
    </ConsoleCard>
  );
}

// Bot token verify + save form, identical to wizard step 1.
// Returns the verified + saved token + username via onSaved so
// the parent can update its state.
function BotTokenField(props: {
  onSaved: (token: string, username: string) => void;
  onCancel: () => void;
}) {
  const [token, setToken] = useState("");
  const [testState, setTestState] = useState<
    "idle" | "testing" | "success" | "error"
  >("idle");
  const [username, setUsername] = useState("");
  const [verifiedToken, setVerifiedToken] = useState<string | null>(null);
  const [testError, setTestError] = useState("");
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");
  const [saveError, setSaveError] = useState("");

  function handleTokenChange(newValue: string) {
    setToken(newValue);
    if (testState === "success" || testState === "error") {
      setTestState("idle");
      setTestError("");
    }
  }

  async function handleTest() {
    setTestState("testing");
    setTestError("");
    try {
      const res = await fetch("/api/onboarding/verify-bot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim() }),
        credentials: "include",
      });
      const data = (await res.json()) as {
        ok: boolean;
        username?: string;
        error?: string;
      };
      if (data.ok && data.username) {
        setTestState("success");
        setUsername(data.username);
        setVerifiedToken(token.trim());
      } else {
        setTestState("error");
        setTestError(data.error ?? "Verification failed");
      }
    } catch (err) {
      setTestState("error");
      setTestError(err instanceof Error ? err.message : "Network error");
    }
  }

  async function handleSave() {
    if (!verifiedToken) return;
    setSaveState("saving");
    setSaveError("");
    try {
      const res = await fetch("/api/onboarding/save-bot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: verifiedToken, username }),
        credentials: "include",
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
      if (data.ok) {
        setSaveState("saved");
        props.onSaved(verifiedToken, username);
      } else {
        setSaveState("error");
        setSaveError(data.error ?? "Save failed");
      }
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : "Network error");
    }
  }

  const canSave =
    testState === "success" &&
    token === verifiedToken &&
    saveState !== "saving";

  return (
    <div className="space-y-2">
      <label
        htmlFor="settings-bot-token"
        className="block text-sm font-medium text-slate-700"
      >
        New Telegram bot token
      </label>
      <div className="flex gap-2">
        <input
          id="settings-bot-token"
          type="password"
          value={token}
          onChange={(e) => handleTokenChange(e.target.value)}
          placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
          autoComplete="off"
          spellCheck={false}
          disabled={saveState === "saved"}
          className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-mono shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none disabled:bg-slate-50"
        />
        <button
          type="button"
          onClick={handleTest}
          disabled={testState === "testing" || !token.trim() || saveState === "saved"}
          className="rounded-lg bg-sky-700 text-white px-3 py-2 text-sm font-medium hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
        >
          {testState === "testing" ? "Testing…" : "Test"}
        </button>
      </div>

      {testState === "success" && (
        <p className="text-sm text-emerald-700">
          ✓ Verified — bot is <span className="font-mono">@{username}</span>
        </p>
      )}
      {testState === "error" && (
        <p className="text-sm text-rose-700">✗ {testError}</p>
      )}

      {testState === "success" && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            className="rounded-md bg-emerald-600 text-white px-4 py-2 text-sm font-medium hover:bg-emerald-700 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            {saveState === "saving"
              ? "Saving…"
              : saveState === "saved"
                ? "Saved ✓"
                : "Save bot token"}
          </button>
          <button
            type="button"
            onClick={props.onCancel}
            disabled={saveState === "saving"}
            className="rounded-md border border-slate-300 bg-white text-slate-700 px-3 py-2 text-sm font-medium hover:bg-slate-50 transition disabled:opacity-50"
          >
            Cancel
          </button>
          {saveState === "error" && (
            <p className="text-sm text-rose-700">✗ {saveError}</p>
          )}
        </div>
      )}

      {testState !== "success" && (
        <button
          type="button"
          onClick={props.onCancel}
          className="text-xs text-slate-500 hover:text-slate-700 transition"
        >
          Cancel
        </button>
      )}
    </div>
  );
}
