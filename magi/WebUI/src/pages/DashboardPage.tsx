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

import ActionItemsPane from "../components/ActionItemsPane";
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
      {/* Light sky-tinted glass strip. Reads as "the sky slightly
          intensified" rather than a dark bar; the body gradient
          shows through. Tabs are sky-blue active, ink-soft idle
          — clean, no dark glass. */}
      <header className="border-b border-sky-light/40 bg-white/60 backdrop-blur-xl">
        <div className="max-w-6xl mx-auto px-6 h-12 flex items-center gap-6">
          <div className="flex items-center gap-2 shrink-0">
            <img
              src="/assets/favicon.svg"
              alt="MAGI"
              width={22}
              height={22}
              className="rounded"
            />
            <span className="brand-lockup">MAGI</span>
          </div>

          <div className="flex-1 flex justify-center">
            <InlineTabBar current={tab} onChange={setTab} />
          </div>

          <div className="flex items-center gap-3 shrink-0">
            <span className="text-xs text-ink-soft hidden sm:inline">
              Signed in as{" "}
              <span className="font-mono text-ink">
                {props.user.display_name ?? props.user.chat_id}
              </span>
            </span>
            <button
              type="button"
              onClick={props.onSignOut}
              className="btn btn-secondary text-xs"
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
            className={`tab-pill tab-pill--on-light ${active ? "is-active" : ""}`}
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
  // Optional — entries with a live component (today:
  // ``action-items`` → ``<ActionItemsPane />``) don't carry
  // a static placeholder. The other "future" entries (Meetings,
  // Reminders, etc.) keep their ``pane`` so a click shows the
  // honest "this isn't wired yet" hint.
  pane?: { title: string; hint: string; meta?: string };
};

const CHAT_CATEGORIES: ChatItem[] = [
  {
    id: "action-items",
    label: "Action Items",
    icon: <IconActionItems />,
    // The pane reads from ``/api/action_items`` at mount —
    // see ``components/ActionItemsPane.tsx``. No static
    // fallback here; an empty list renders as "没有待办".
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

/** Cap the visible history list at 20 — beyond that, the "查看全部"
 *  row is the affordance to widen the window.
 *
 *  D.6: actually loaded from
 *  ``GET /api/chat/sessions?limit=50`` now; the cap of 20 is
 *  purely a UI cap (the sidebar shows the first 20 with a
 *  "load more" expansion when the server has more). */
const HISTORY_VISIBLE_LIMIT = 20;

/** Storage key for the active chat session id. We keep the
 *  *just-opened* session in localStorage so a hard refresh
 *  restores the live thread. The backend is the source of
 *  truth — localStorage is just a "last known" pointer. */
const SESSION_STORAGE_KEY = "magi_chat_session_id";

/** A row in the ``/api/chat/sessions`` list response. */
type SessionSummary = {
  session_id: string;
  created_at: string;
  created_by_employee_id: number;
  updated_at: string;
  message_count: number;
  preview: string;
};

function ChatTab() {
  // "view-all" is a synthetic id that aliases the search view (per
  // the design — clicking the last row in the history list should
  // behave like opening search).
  const [selectedId, setSelectedId] = useState<string>(CHAT_CATEGORIES[0].id);

  // -- session lifecycle (D.6) -----------------------------------
  // ``sessionId`` is the file-backed chat thread the operator
  // currently has open. ``null`` means "no session yet" (the
  // next /send call will auto-create one); the server
  // returns the new id in the response.
  const [sessionId, setSessionId] = useState<string | null>(
    () => localStorage.getItem(SESSION_STORAGE_KEY)
  );
  // History list — most recent first, scoped to the
  // current chat_id (server resolves via cookie).
  const [history, setHistory] = useState<SessionSummary[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyLimit] = useState(50);
  const [historyLoading, setHistoryLoading] = useState(false);
  // C7 / future: server returns a chunked list and the
  // UI exposes "load more". v0 always renders the first
  // 20 of whatever the server sends.
  const [historyExpanded, setHistoryExpanded] = useState(false);

  // -- chat messages (kept in component state, hydrated from
  //    the server on session switch) ------------------------------
  const [chatMessages, setChatMessages] = useState<
    Array<{ id: number; role: "user" | "assistant"; text: string }>
  >([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  // ``chatError`` carries the stable backend error ``code`` so
  // the renderer can pick a friendlier message than the English
  // ``detail`` for known cases (e.g. ``chat.llm_credentials_required``
  // points the operator at the Organization tab where their
  // per-employee LLM is configured). Unknown codes fall through
  // to ``detail`` so a missing translation never blanks the UI.
  const [chatError, setChatError] = useState<
    { code: string; detail: string } | null
  >(null);

  // -- helpers -----------------------------------------------------

  async function loadSession(id: string) {
    setChatError(null);
    const r = await fetch(`/api/chat/sessions/${id}`, {
      credentials: "include",
    });
    if (!r.ok) {
      // 404 → stale id (manually deleted or migrated).
      // Drop the localStorage pointer so the next send
      // auto-creates; clear messages so the operator
      // sees an empty thread instead of a flash of
      // someone else's content.
      if (r.status === 404) {
        localStorage.removeItem(SESSION_STORAGE_KEY);
        setSessionId(null);
        setChatMessages([]);
        setHistory((h) => h.filter((x) => x.session_id !== id));
        return;
      }
      const body = (await r.json().catch(() => ({}))) as {
        code?: string;
        detail?: string;
      };
      setChatError({
        code: body.code ?? "unknown",
        detail: body.detail ?? `Load failed (${r.status})`,
      });
      return;
    }
    const data = (await r.json()) as {
      session_id: string;
      messages: Array<{ message_id: string; role: string; text: string; ts: string }>;
    };
    setSessionId(data.session_id);
    localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
    setChatMessages(
      data.messages.map((m, i) => ({
        // idx is fine for keys — reassignment is rare
        // and the messages array fully replaces on load.
        id: i,
        role: m.role as "user" | "assistant",
        text: m.text,
      }))
    );
  }

  async function refreshHistory() {
    setHistoryLoading(true);
    try {
      const r = await fetch(
        `/api/chat/sessions?limit=${historyLimit}&offset=0`,
        { credentials: "include" }
      );
      if (!r.ok) return;
      const data = (await r.json()) as {
        items: SessionSummary[];
        total: number;
      };
      setHistory(data.items);
      setHistoryTotal(data.total);
    } finally {
      setHistoryLoading(false);
    }
  }

  function newChat() {
    setSessionId(null);
    setChatMessages([]);
    setChatInput("");
    setChatError(null);
    localStorage.removeItem(SESSION_STORAGE_KEY);
    // The next /send call will get a fresh session_id back
    // from the server.
  }

  async function openSession(id: string) {
    await loadSession(id);
    setSelectedId("new-chat");
    void refreshHistory();
  }

  async function deleteSession(id: string) {
    if (!confirm("删除这条对话？")) return;
    const r = await fetch(`/api/chat/sessions/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (r.ok || r.status === 404) {
      // Filter locally; ignore server states (the route is
      // idempotent, so 200 / 204 / 404 all mean "gone").
      setHistory((h) => h.filter((x) => x.session_id !== id));
      setHistoryTotal((t) => Math.max(0, t - 1));
      // If we just deleted the active session, drop the
      // localStorage pointer and start fresh — the next
      // /send will auto-create.
      if (id === sessionId) {
        localStorage.removeItem(SESSION_STORAGE_KEY);
        setSessionId(null);
        setChatMessages([]);
      }
    }
  }

  // -- mount effects -----------------------------------------------

  // On first mount, hydrate the active session from
  // localStorage. If the id no longer exists, ``loadSession``
  // drops the pointer and starts clean.
  useEffect(() => {
    const id = localStorage.getItem(SESSION_STORAGE_KEY);
    if (id) loadSession(id);
    void refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function sendChat() {
    const text = chatInput.trim();
    if (!text || chatSending) return;
    setChatInput("");
    setChatError(null);
    const userMsg = { id: Date.now(), role: "user" as const, text };
    setChatMessages((prev) => [...prev, userMsg]);
    setChatSending(true);
    try {
      const r = await fetch("/api/chat/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, session_id: sessionId }),
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          code?: string;
          detail?: string;
        };
        setChatError({
          code: body.code ?? "unknown",
          detail: body.detail ?? `Send failed (${r.status})`,
        });
        return;
      }
      const data = (await r.json()) as { reply: string; session_id: string };
      // Pin the session id on the first send (the server
      // auto-created one) and refresh the history so the
      // sidebar reflects the freshly-persisted thread.
      if (data.session_id !== sessionId) {
        setSessionId(data.session_id);
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
        void refreshHistory();
      }
      setChatMessages((prev) => [
        ...prev,
        { id: Date.now() + 1, role: "assistant", text: data.reply },
      ]);
    } catch (err) {
      setChatError({
        code: "network",
        detail: err instanceof Error ? err.message : "Network error",
      });
    } finally {
      setChatSending(false);
    }
  }

  const allById: Record<string, ChatItem> = {};
  for (const c of CHAT_CATEGORIES) allById[c.id] = c;
  for (const a of CHAT_ACTIONS) allById[a.id] = a;
  // ``HISTORY`` (the placeholder) is intentionally not
  // merged in anymore — D.6 replaces it with a real list
  // driven from ``/api/chat/sessions``. The right-pane
  // "view-all" / "search" entry is still synthetic.
  allById["view-all"] = allById["search"];

  const selected = allById[selectedId] ?? CHAT_CATEGORIES[0];
  // The sidebar's "历史对话" list — latest first, the first
  // ``HISTORY_VISIBLE_LIMIT`` of the 50 the server sent. Each
  // row shows the first user-message preview as a label;
  // clicking opens that session in the chat pane; the "×"
  // button deletes it (with a confirm).
  const historyVisible = history.slice(0, HISTORY_VISIBLE_LIMIT);
  const historyOverflow = Math.max(0, historyTotal - historyVisible.length);

  return (
    <SidebarShell
      items={[...CHAT_CATEGORIES, ...CHAT_ACTIONS]}
      selectedId={selectedId}
      onSelect={setSelectedId}
      ariaLabel="Chat navigation"
      belowItems={
        <>
          <hr className="my-3 border-sky-light/40" />
          <p className="mt-1 mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-ocean/70">
            历史对话
          </p>
          {historyLoading && history.length === 0 ? (
            <p className="px-3 text-xs text-ink-soft">Loading…</p>
          ) : history.length === 0 ? (
            <p className="px-3 text-xs text-ink-soft">
              No conversations yet.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {historyVisible.map((h) => (
                <li
                  key={h.session_id}
                  className={
                    "flex items-center gap-1 rounded-md transition " +
                    (h.session_id === sessionId
                      ? "bg-sky-deep text-white"
                      : "text-ocean hover:bg-sky-light/60 hover:text-sky-deep")
                  }
                >
                  <button
                    type="button"
                    onClick={() => openSession(h.session_id)}
                    className="flex-1 text-left px-3 py-1.5 text-xs truncate"
                    title={h.preview || "(空对话)"}
                  >
                    {h.preview || "(空对话)"}{" "}
                    <span className={h.session_id === sessionId ? "opacity-70" : "opacity-60"}>
                      · {h.message_count}条
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteSession(h.session_id)}
                    className={
                      "px-2 py-1.5 text-xs " +
                      (h.session_id === sessionId
                        ? "text-white/80 hover:text-white"
                        : "text-ocean/60 hover:text-sky-deep")
                    }
                    title="删除"
                    aria-label="删除对话"
                  >
                    ✕
                  </button>
                </li>
              ))}
              {historyOverflow > 0 && (
                <button
                  type="button"
                  onClick={() => setHistoryExpanded((b) => !b)}
                  className="mt-1 w-full text-left px-3 py-1.5 text-xs text-sky-deep hover:text-sky-mid"
                >
                  {historyExpanded
                    ? "收起"
                    : `查看更多 (${historyOverflow}) →`}
                </button>
              )}
            </ul>
          )}
          <button
            type="button"
            onClick={() => setSelectedId("view-all")}
            className={
              "mt-1 w-full text-left px-3 py-1.5 rounded-md text-xs transition " +
              (selectedId === "view-all"
                ? "bg-sky-deep text-white"
                : "text-sky-deep hover:text-sky-mid hover:bg-sky-light/40")
            }
          >
            查看全部 →
          </button>
        </>
      }
    >
      {selectedId === "new-chat" ? (
        <ChatConversationPane
          messages={chatMessages}
          input={chatInput}
          onInputChange={setChatInput}
          sending={chatSending}
          error={chatError}
          onSend={sendChat}
          onNewChat={newChat}
          hasActiveSession={sessionId !== null}
        />
      ) : selectedId === "action-items" ? (
        <ActionItemsPane />
      ) : selected.pane ? (
        <div className="p-8 text-center flex flex-col items-center justify-center">
          <h2 className="text-lg font-semibold text-ink">{selected.pane.title}</h2>
          <p className="mt-2 text-sm text-ink-soft max-w-md">{selected.pane.hint}</p>
          {selected.pane.meta && (
            <p className="mt-3 text-xs text-ink-soft">{selected.pane.meta}</p>
          )}
        </div>
      ) : null}
    </SidebarShell>
  );
}

// -- pane: chat conversation ----------------------------------------------
//
// v0 chat UI: scrollable message list (user right, assistant
// left) + a textarea at the bottom + a Send button. The
// conversation lives in ChatTab's state — refreshing the
// page clears it. C7 wires this to a real conversation
// store + streaming replies.
//
// The textarea submits on Cmd/Ctrl-Enter so Enter stays
// available for newlines (chat-style). The Send button is
// disabled while a request is in flight so the user can't
// double-submit.

type ChatMessageRow = {
  id: number;
  role: "user" | "assistant";
  text: string;
};

function ChatConversationPane(props: {
  messages: ChatMessageRow[];
  input: string;
  onInputChange: (v: string) => void;
  sending: boolean;
  error: { code: string; detail: string } | null;
  onSend: () => void;
  /** D.6: drop the local session pointer + messages. */
  onNewChat: () => void;
  /** D.6: whether the operator has a live session currently
   *  open. The "新对话" button is meaningless when there's
   *  no thread — disable it. */
  hasActiveSession: boolean;
}) {
  return (
    <div className="flex flex-col h-[560px]">
      {/* Header — the pane title plus a "新对话" affordance
          that drops the active session (the next /send
          auto-creates a fresh one). The button is disabled
          when there's no active session. */}
      <div className="px-6 py-3 border-b border-sky-light/40 flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <h2 className="text-base font-semibold text-ink">新对话</h2>
          <p className="text-xs text-ink-soft">
            跟系统 LLM 直接对话。回复会用 SOUL.md 里定义的 persona。
          </p>
        </div>
        <button
          type="button"
          onClick={props.onNewChat}
          disabled={!props.hasActiveSession}
          className="btn btn-secondary text-xs disabled:opacity-50 disabled:cursor-not-allowed"
          title="开一条新对话（清空当前消息）"
        >
          新对话
        </button>
      </div>

      {/* Message list. Empty state nudges the operator to type
          something; once the first message lands, the list
          owns the rest of the pane height. */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {props.messages.length === 0 ? (
          <p className="text-sm text-ink-soft text-center mt-12">
            输入消息开始对话。回车换行，⌘/Ctrl + 回车发送。
          </p>
        ) : (
          props.messages.map((m) => (
            <div
              key={m.id}
              className={
                "flex " +
                (m.role === "user" ? "justify-end" : "justify-start")
              }
            >
              <div
                className={
                  "max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap " +
                  (m.role === "user"
                    ? "bg-sky-deep text-white"
                    : "bg-sky-pale/60 text-ink border border-sky-light/40")
                }
              >
                {m.text}
              </div>
            </div>
          ))
        )}
        {props.sending && (
          <div className="flex justify-start">
            <div className="rounded-2xl bg-sky-pale/60 text-ink-soft border border-sky-light/40 px-4 py-2.5 text-sm">
              <span className="inline-flex gap-1">
                <span className="animate-pulse">·</span>
                <span className="animate-pulse [animation-delay:120ms]">
                  ·
                </span>
                <span className="animate-pulse [animation-delay:240ms]">
                  ·
                </span>
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Error banner — surfaces under the message list so
          the user keeps context. Clears on the next send.

          Known backend ``code``s get a friendlier Chinese line
          that tells the operator how to fix it (rather than
          just dumping the English ``detail``); unknown codes
          fall through to ``detail`` so a missing translation
          never blanks the UI. */}
      {props.error && (() => {
        const friendly: Record<string, string> = {
          // The operator hasn't set their per-employee LLM
          // credentials yet — point them at the Organization
          // tab where the employee detail panel lives. Tells
          // them what to fill in (provider + API key) so the
          // next attempt works without a second round-trip.
          chat_llm_credentials_required:
            "还没设置你的 LLM provider 和 API key。切到「员工」tab，找到自己的档案，把 Provider 和 API Key 填上再发消息。",
          // ``chat.unknown_sender`` would mean the cookie is
          // unbound, which the auth gate catches first — keep
          // a local string here in case the gate is ever
          // bypassed and the chat endpoint surfaces this.
          chat_unknown_sender:
            "登录失效了，重新登录一次再试试。",
          // 401 from the auth gate — typically a stale
          // session after long idle.
          auth_not_signed_in:
            "登录失效了，重新登录一次再试试。",
        };
        // Map dot-style backend codes to underscore keys
        // (the friendly table) since this object uses
        // underscore keys.
        const key = props.error.code.replace(/\./g, "_");
        const message = friendly[key] ?? props.error.detail;
        return (
          <div className="mx-6 mb-2">
            <p className="form-error">✗ {message}</p>
          </div>
        );
      })()}

      {/* Composer — textarea + send button. The button
          stays enabled when input is empty too; the onSend
          handler early-returns on whitespace-only input so
          an accidental click does nothing. */}
      <div className="border-t border-sky-light/40 px-6 py-3 bg-white/40">
        <div className="flex items-end gap-2">
          <textarea
            value={props.input}
            onChange={(e) => props.onInputChange(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                props.onSend();
              }
            }}
            placeholder="输入消息…"
            rows={2}
            disabled={props.sending}
            className="form-input flex-1 text-sm py-2 px-3 resize-none"
            style={{ minHeight: "44px", maxHeight: "160px" }}
          />
          <button
            type="button"
            onClick={props.onSend}
            disabled={props.sending || !props.input.trim()}
            className="btn btn-primary text-sm py-2 px-4"
          >
            {props.sending ? "发送中…" : "发送"}
          </button>
        </div>
      </div>
    </div>
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
  // Soft-delete flag — ISO timestamp string, ``null`` means
  // the employee is active. Surfaced as a "已离职" badge in
  // the table; flip via the detail panel.
  separated_at: string | null;
  // Per-MAGI-perspective role: ``admin`` signs in to
  // Adam's WebUI; ``assigned`` is the employee this MAGI
  // serves; ``employee`` / ``guest`` are reserved for the
  // cross-MAGI future (C6+).
  role: "admin" | "assigned" | "employee" | "guest";
  // Bound TG chat id, when known. ``null`` until the
  // binding flow runs (C2 self-serve, or the admin endpoint
  // for v0). Unique across the company.
  telegram_id: number | null;
};

// Mirrors the API's ``EmployeeListOut`` shape — the page
// slice plus the totals the pager needs.
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

function DepartmentsPane() {
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
                              isCollapsed ? "expand children" : "collapse children"
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
          setDetailError("Telegram chat_id 必须是整数");
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
            aria-label="Employee scope"
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
                      Telegram chat_id
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
    <div className="mt-4 rounded-lg border border-sky-light/40 bg-white/60 p-3">
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
          className="form-input flex-1 text-sm py-2 px-3 font-mono"
        />
        <button
          type="button"
          onClick={sendCode}
          disabled={
            state === "sending" ||
            state === "verifying" ||
            !chatId.trim()
          }
          className="btn btn-primary text-sm py-2 px-3 shrink-0"
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
          className="btn btn-secondary text-sm py-2 px-2 shrink-0"
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
            className="form-input flex-1 text-sm py-2 px-3 font-mono tracking-widest"
            disabled={state === "verifying"}
          />
          <button
            type="button"
            onClick={verifyCode}
            disabled={state === "verifying" || code.length !== 6}
            className="btn btn-primary text-sm py-2 px-3 shrink-0"
          >
            {state === "verifying" ? "Verifying…" : "Verify"}
          </button>
        </div>
      )}

      {state === "error" && error && (
        <p className="form-error mt-2 text-xs">✗ {error}</p>
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
        <h2 className="text-lg font-semibold text-ink">Skills</h2>
        <p className="mt-1 text-sm text-ink-soft">
          Reusable actions EVEs can call — schedule reminders,
          book meetings, search the knowledge base, collect info.
          The 4 MVP skills land with C4.
        </p>
        <p className="mt-2 text-xs text-ink-soft">C4 — Skill runner + 4 MVP skills</p>
      </div>
      <ConsoleCard title="Registry">
        <p className="text-sm text-ink-soft">0 skills registered</p>
        <p className="mt-1 text-xs text-ink-soft">
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
        <h2 className="text-lg font-semibold text-ink">Connectors</h2>
        <p className="mt-1 text-sm text-ink-soft">
          Channels EVEs talk through. Each connector is one
          platform; nodes mount the subset they need.
        </p>
        <p className="mt-2 text-xs text-ink-soft">Phase 2 — Email / Calendar</p>
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
      : "bg-sky-pale/40 text-ink-soft border-sky-light/40";
  const label = props.status === "connected" ? "connected" : "coming soon";
  return (
    <div className="rounded-lg border border-sky-light/40 bg-white/60 p-3 flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-ink">{props.name}</div>
        <div className="text-xs text-ink-soft">{props.note}</div>
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
        <h2 className="text-lg font-semibold text-ink">Contacts</h2>
        <p className="mt-1 text-sm text-ink-soft">
          The company directory — every employee an EVE can reach.
          Scoped per employee; each row carries display name,
          department, role, and contact channels.
        </p>
        <p className="mt-2 text-xs text-ink-soft">
          C1.1 — ORM + directory CRUD
        </p>
      </div>
      <ConsoleCard title="Directory">
        <p className="text-sm text-ink-soft">0 employees</p>
        <p className="mt-1 text-xs text-ink-soft">
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
      <p className="text-sm text-ink-soft">
        Platform adapters the node can mount. WebUI is the
        console you're using; Telegram is the IM channel the
        wizard configured. The rest are planned.
      </p>

      <table className="w-full text-sm mt-4">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
            <th className="py-2 pr-4 font-medium">Name</th>
            <th className="py-2 pr-4 font-medium w-32">Status</th>
            <th className="py-2 pr-4 font-medium">Notes</th>
            <th className="py-2 font-medium w-24 text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-sky-light/30">
            <td className="py-2 pr-4 text-ink">WebUI</td>
            <td className="py-2 pr-4">
              <ChannelStatusBadge status="connected" />
            </td>
            <td className="py-2 pr-4 text-ink-soft font-mono text-xs">
              :42069
            </td>
            <td className="py-2 text-right text-xs text-ink-soft">—</td>
          </tr>

          <tr className="border-b border-sky-light/30">
            <td className="py-2 pr-4 text-ink">Telegram</td>
            <td className="py-2 pr-4">
              <ChannelStatusBadge
                status={tgConnected ? "connected" : "disconnected"}
              />
            </td>
            <td className="py-2 pr-4 text-ink-soft font-mono text-xs">
              {tgNote}
            </td>
            <td className="py-2 text-right">
              {tgConnected && !editing && (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="text-sm text-sky-700 hover:text-sky-deep transition"
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
        <div className="mt-4 border-t border-sky-light/40 pt-4">
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
    <tr className="border-b border-sky-light/30 last:border-0 opacity-50">
      <td className="py-2 pr-4 text-ink-soft">{props.name}</td>
      <td className="py-2 pr-4">
        <ChannelStatusBadge status="coming" />
      </td>
      <td className="py-2 pr-4 text-ink-soft">—</td>
      <td className="py-2 text-right text-xs text-ink-soft">—</td>
    </tr>
  );
}

function ChannelStatusBadge(props: {
  status: "connected" | "disconnected" | "coming";
}) {
  switch (props.status) {
    case "connected":
      return (
        <span className="status-pill status-pill--connected">
          connected
        </span>
      );
    case "disconnected":
      return (
        <span className="status-pill status-pill--disconnected">
          disconnected
        </span>
      );
    case "coming":
      return (
        <span className="text-xs text-ink-soft bg-sky-pale/40 border border-sky-light/40 rounded px-1.5 py-0.5">
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
  // WebUI Access = employees WHERE role=admin. The unified
  // table means a single GET returns the list, the new
  // employees / remove flow can delete rows directly, and
  // we don't have to keep two views in sync.
  const [admins, setAdmins] = useState<EmployeeRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [addingNew, setAddingNew] = useState(false);

  async function refresh() {
    setLoadError(null);
    try {
      const r = await fetch(
        "/api/employees?role=admin&page=1&page_size=100",
        { credentials: "include" },
      );
      if (!r.ok) {
        setLoadError("Failed to load access list");
        return;
      }
      const data = (await r.json()) as {
        items: EmployeeRow[];
        total: number;
      };
      setAdmins(data.items);
      // Bubble the updated admin list up to App so the rest of
      // the dashboard (header, etc.) stays consistent.
      props.onAdminsChanged(
        data.items
          .filter((e) => e.telegram_id !== null)
          .map((e) => ({
            chatId: String(e.telegram_id),
            displayName: e.display_name ?? e.name,
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

  async function handleRemoveAdmin(emp: EmployeeRow) {
    if (String(emp.telegram_id ?? "") === props.signedInUser.chat_id) {
      return; // belt + suspenders
    }
    if (
      !confirm(
        `确定移除管理员「${emp.name}」？这会从 employees 表删掉这一行。`,
      )
    ) {
      return;
    }
    // Re-saving the full list (minus this one) is the
    // current API surface; it also drops the Employee row
    // because the new save-admin deletes admins not in the
    // incoming set.
    const remaining =
      (admins ?? [])
        .filter((e) => e.id !== emp.id && e.telegram_id !== null)
        .map((e) => String(e.telegram_id));
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

  return (
    <ConsoleCard title="WebUI Access">
      <p className="text-sm text-ink-soft">
        Sign-in list. Each row is an <code>Employee</code> with
        <span className="font-medium"> role=admin</span> and a
        bound <code>telegram_id</code>. The wizard
        (step 3) creates these from the verified chat_ids;
        the table below mirrors that state. Removing a row
        calls the same wizard endpoint with the smaller list
        — the server drops the deleted rows from the
        employees table.
      </p>

      <div className="mt-4">
        {admins === null && !loadError && (
          <p className="text-sm text-ink-soft">Loading…</p>
        )}
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {admins !== null && admins.length === 0 && (
          <p className="text-sm text-ink-soft">
            No one has access yet. Run the first-time wizard
            to add a super admin.
          </p>
        )}
        {admins !== null && admins.length > 0 && (
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium w-44">Role</th>
                <th className="py-2 pr-4 font-medium">TG chat_id</th>
                <th className="py-2 font-medium w-28 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {admins.map((emp) => {
                const isSelf =
                  String(emp.telegram_id ?? "") ===
                  props.signedInUser.chat_id;
                return (
                  <tr key={emp.id} className="">
                    <td className="py-2 pr-4 text-ink">
                      {emp.display_name ?? emp.name}
                    </td>
                    <td className="py-2 pr-4">
                      <RoleBadge role={emp.role} />
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-ink-soft">
                      {emp.telegram_id ?? (
                        <span className="text-ink-soft">—</span>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      {isSelf ? (
                        <span className="status-pill status-pill--connected">
                          you
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => handleRemoveAdmin(emp)}
                          title="Remove this super admin"
                          className="btn btn-secondary text-xs py-1 px-2"
                        >
                          ✕ Remove
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {!addingNew && (
          <button
            type="button"
            onClick={() => setAddingNew(true)}
            className="mt-3 text-sm text-sky-700 hover:text-sky-deep transition"
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

function RoleBadge(props: {
  role: "admin" | "assigned" | "employee" | "guest";
}) {
  switch (props.role) {
    case "admin":
      return (
        <span className="text-xs text-ink-soft bg-sky-pale/40 border border-sky-light/40 rounded px-1.5 py-0.5">
          super admin
        </span>
      );
    case "assigned":
      return (
        <span className="text-xs text-white bg-sky-deep border border-sky-deep rounded px-1.5 py-0.5">
          assigned
        </span>
      );
    case "employee":
      return (
        <span className="text-xs text-ink-soft bg-white border border-sky-light/40 rounded px-1.5 py-0.5">
          employee
        </span>
      );
    case "guest":
      return (
        <span className="text-xs text-ink-soft bg-sky-pale/60 border border-sky-light/40 rounded px-1.5 py-0.5">
          guest
        </span>
      );
  }
}

function SettingsOnboardingCard(props: { onRestart: () => void }) {
  return (
    <ConsoleCard title="Onboarding">
      <p className="text-sm text-ink-soft">
        Re-run the first-time setup wizard. Saved bot and admin
        rows stay in SQLite; the wizard will resume from wherever
        it left off.
      </p>
      <button
        type="button"
        onClick={props.onRestart}
        className="mt-3 btn btn-secondary px-4 py-2"
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
      <label htmlFor="settings-bot-token" className="form-label">
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
          className="form-input flex-1 text-sm py-2 px-3 font-mono"
        />
        <button
          type="button"
          onClick={handleTest}
          disabled={testState === "testing" || !token.trim() || saveState === "saved"}
          className="btn btn-primary text-sm py-2 px-3 shrink-0"
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
        <p className="form-error">✗ {testError}</p>
      )}

      {testState === "success" && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            className="btn btn-primary text-sm py-2 px-4"
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
            className="btn btn-ghost text-sm py-2 px-3"
          >
            Cancel
          </button>
          {saveState === "error" && (
            <p className="form-error">✗ {saveError}</p>
          )}
        </div>
      )}

      {testState !== "success" && (
        <button
          type="button"
          onClick={props.onCancel}
          className="text-xs text-ink-soft hover:text-sky-deep transition"
        >
          Cancel
        </button>
      )}
    </div>
  );
}
