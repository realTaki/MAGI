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
function DepartmentsPane() {
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">部门管理</h2>
          <p className="mt-1 text-sm text-slate-600">
            Create departments, name a lead, and add employees to
            each. The lead ("负责人") is whichever employee is
            flagged as the dept's manager.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            disabled
            title="C1.1"
            className="rounded-md bg-sky-700 text-white px-4 py-2 text-sm font-medium shadow-sm hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            + Create department
          </button>
        </div>
      </div>

      <ConsoleCard title="">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-slate-500 border-b border-slate-200">
              <th className="py-2 pr-4 font-medium">部门名称</th>
              <th className="py-2 pr-4 font-medium w-24">部门人数</th>
              <th className="py-2 pr-4 font-medium">负责人</th>
              <th className="py-2 font-medium w-24 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan={4} className="py-6 text-center text-slate-400 text-xs">
                No departments yet — C1.1 (ORM + directory CRUD) fills
                this in.
              </td>
            </tr>
          </tbody>
        </table>
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
function EmployeesPane() {
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">员工管理</h2>
          <p className="mt-1 text-sm text-slate-600">
            Every employee an EVE can be assigned to. Adding a row
            here is what makes "派发 EVE 给某人" (C6) possible.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            disabled
            title="C1.1"
            className="rounded-md bg-sky-700 text-white px-4 py-2 text-sm font-medium shadow-sm hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            + Add employee
          </button>
        </div>
      </div>

      <ConsoleCard title="">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-slate-500 border-b border-slate-200">
              <th className="py-2 pr-4 font-medium">姓名</th>
              <th className="py-2 pr-4 font-medium">TG chat_id</th>
              <th className="py-2 pr-4 font-medium">部门</th>
              <th className="py-2 pr-4 font-medium w-20">状态</th>
              <th className="py-2 font-medium w-24 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan={5} className="py-6 text-center text-slate-400 text-xs">
                No employees yet — C1.1 fills this in. C2 then adds
                the TG-binding step (employee proves ownership of
                the chat).
              </td>
            </tr>
          </tbody>
        </table>
      </ConsoleCard>
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
  const [displayName, setDisplayName] = useState<string | null>(null);

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
        setDisplayName(data.display_name ?? null);
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
