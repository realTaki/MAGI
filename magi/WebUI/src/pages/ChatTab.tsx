/**
 * ChatTab — chat session list + conversation pane.
 *
 * Two-column shell: sidebar on the left (6 EVE-output
 * categories at the top, then a separator, then
 * 新对话 / 搜索对话 / 历史对话 with a top-20 list
 * and a 查看全部 affordance), and a content pane on
 * the right that changes based on what's selected.
 *
 * SidebarItem.label convention in this file: dotted
 * i18n keys (`sidebar.actionItems`, `sidebar.meetings`).
 * The shell passes them through verbatim. (Org /
 * Knowledge use raw Chinese; Settings resolves keys in
 * the renderer — see plan TODO.)
 */
import { useEffect, useRef, useState } from "react";

import ActionItemsPane from "./chat/ActionItemsPane";
import ChatSearchPane from "./chat/ChatSearchPane";
import TaskListPane from "./chat/TaskListPane";
import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import {
  IconActionItems,
  IconDailyReports,
  IconEmail,
  IconMeetings,
  IconPlus,
  IconReminders,
  IconScheduledTasks,
  IconSearch,
} from "../components/icons";
import { useT } from "../i18n/index";

import { ChatConversationPane } from './chat/ChatConversationPane';


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

// Static sidebar config. Strings are i18n keys, resolved at
// render via ``t()`` — module-level constants can't call
// hooks directly so we resolve at the render site instead.
// ``pane.title`` and ``pane.hint`` are still raw strings
// because they're operator-facing descriptions of features
// that aren't yet wired (see the comments above); translating
// them is fine to leave for later.
const CHAT_CATEGORIES: ChatItem[] = [
  {
    id: "action-items",
    label: "sidebar.actionItems",
    icon: <IconActionItems />,
    pane: {
      title: "sidebar.actionItems",
      hint: "sidebar.actionItemsHint",
    },
  },
  {
    id: "meetings",
    label: "sidebar.meetings",
    icon: <IconMeetings />,
    pane: {
      title: "sidebar.meetings",
      hint: "sidebar.meetingsHint",
      meta: "C4",
    },
  },
  {
    id: "reminders",
    label: "sidebar.reminders",
    icon: <IconReminders />,
    pane: {
      title: "sidebar.reminders",
      hint: "sidebar.remindersHint",
      meta: "C5",
    },
  },
  {
    id: "email",
    label: "sidebar.email",
    icon: <IconEmail />,
    pane: {
      title: "sidebar.email",
      hint: "sidebar.emailHint",
      meta: "Phase 2",
    },
  },
  {
    id: "scheduled-tasks",
    label: "sidebar.tasks",
    icon: <IconScheduledTasks />,
  },
  {
    id: "daily-reports",
    label: "sidebar.reports",
    icon: <IconDailyReports />,
    pane: {
      title: "sidebar.reports",
      hint: "sidebar.reportsHint",
      meta: "C5",
    },
  },
];

const CHAT_ACTIONS: ChatItem[] = [
  {
    id: "new-chat",
    label: "sidebar.newChat",
    icon: <IconPlus />,
    pane: {
      title: "sidebar.newChat",
      hint: "sidebar.newChatHint",
      meta: "C3 / C6",
    },
  },
  {
    id: "search",
    label: "sidebar.search",
    icon: <IconSearch />,
    pane: {
      title: "sidebar.search",
      hint: "sidebar.searchHint",
      meta: "D.18",
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
  created_by_uid: number;
  updated_at: string;
  message_count: number;
  preview: string;
  // D.7 — manual rename via PATCH /api/chat/sessions/{id},
  // or auto-generated by the background worker. ``null``
  // means "no title yet — fall back to preview".
  title: string | null;
};

export default function ChatTab() {
  // "view-all" is a synthetic id that aliases the search view (per
  // the design — clicking the last row in the history list should
  // behave like opening search).
  const t = useT();
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
  // current tgid (server resolves via cookie).
  const [history, setHistory] = useState<SessionSummary[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyLimit] = useState(50);
  const [historyLoading, setHistoryLoading] = useState(false);
  // C7 / future: server returns a chunked list and the
  // UI exposes "load more". v0 always renders the first
  // 20 of whatever the server sends.
  const [historyExpanded, setHistoryExpanded] = useState(false);
  // D.7 — manual rename UI. When ``editing.id`` matches a
  // row, that row swaps its label button for an ``<input>``.
  // Only one row can be in edit mode at a time (cheaper
  // than a Set<id>).
  const [editing, setEditing] = useState<
    { id: string; value: string } | null
  >(null);

  // -- chat messages (kept in component state, hydrated from
  //    the server on session switch) ------------------------------
  const [chatMessages, setChatMessages] = useState<
    Array<{ id: number; role: "user" | "assistant"; text: string }>
  >([]);
  // D.18+2: pagination state for the chat pane.
  //
  // ``loadedCount`` is the number of active messages currently
  // rendered in ``chatMessages``; ``totalActive`` is the
  // server-reported count of all active rows in the
  // session. ``loadedCount < totalActive`` means older
  // messages are still on the server, and the chat pane
  // surfaces a "加载更早消息" affordance at the top.
  //
  // Why two separate state slots rather than comparing
  // ``chatMessages.length`` to a single ``total``:
  //   - ``chatMessages`` is replaced wholesale on session
  //     switch (see ``loadSession``) — its length is the
  //     count of *currently-rendered* rows, which equals
  //     ``loadedCount`` until the operator clicks the
  //     load-more button.
  //   - ``totalActive`` survives a session switch's reset
  //     so a fast open-and-close doesn't accidentally
  //     hide "more available" mid-render.
  //
  // ``pagingOlder`` is the in-flight flag for the
  // load-more fetch — disables the button so a slow
  // network doesn't trigger two parallel requests.
  const [loadedCount, setLoadedCount] = useState(0);
  const [totalActive, setTotalActive] = useState(0);
  const [pagingOlder, setPagingOlder] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  // ``chatError`` carries the stable backend error ``code`` so
  // the renderer can pick a friendlier message than the English
  // ``detail`` for known cases (e.g. ``chat.llm_credentials_required``
  // points the operator at the Organization tab where their
  // per-contact LLM is configured). Unknown codes fall through
  // to ``detail`` so a missing translation never blanks the UI.
  const [chatError, setChatError] = useState<
    { code: string; detail: string } | null
  >(null);

  // D.8: pane header follows the active session. Both cleared
  // together whenever ``sessionId`` changes (see ``newChat`` /
  // ``openSession`` / ``loadSession``); never decoupled.
  const [activeTitle, setActiveTitle] = useState<string | null>(null);
  const [activePreview, setActivePreview] = useState<string | null>(null);

  // -- helpers -----------------------------------------------------

  async function loadSession(id: string) {
    setChatError(null);
    // Reset pagination state on session switch — the next
    // fetch starts at the latest page (offset 0 = newest
    // tail). The previous session's older-page history is
    // dropped along with the rendered messages.
    setLoadedCount(0);
    setTotalActive(0);

    // D.18+2 — switch to the paginated messages endpoint
    // (the full-session endpoint is still useful for the
    // audit / "view full transcript" UI; for the chat pane
    // we want a single round-trip on open + lazy "load
    // older" on demand).
    const PAGE_SIZE = 50;
    const r = await fetch(
      `/api/chat/sessions/${id}/messages?limit=${PAGE_SIZE}&offset=0`,
      { credentials: "include" },
    );
    if (!r.ok) {
      if (r.status === 404) {
        // 404 → stale id (manually deleted or migrated).
        // Drop the localStorage pointer so the next send
        // auto-creates; clear messages so the operator
        // sees an empty thread instead of a flash of
        // someone else's content.
        localStorage.removeItem(SESSION_STORAGE_KEY);
        setSessionId(null);
        setChatMessages([]);
        // D.8: matching clear — header reverts to default.
        setActiveTitle(null);
        setActivePreview(null);
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
      total_active: number;
    };
    setSessionId(data.session_id);
    localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
    setChatMessages(
      data.messages.map((m, i) => ({
        // id starts at 0 for the newest page; a future
        // loadOlder() prepends older messages with negative
        // ids so they sort below the current ones. The
        // React key uses index-in-array anyway so we
        // could go either way; negative ids just make
        // intent obvious in dev tools.
        id: i,
        role: m.role as "user" | "assistant",
        text: m.text,
      })),
    );
    setLoadedCount(data.messages.length);
    setTotalActive(data.total_active);
    // D.8: capture the session's own title + first user message
    // so the pane header can render them. We don't get the
    // title from the paginated endpoint, so fall back to a
    // separate ``/api/chat/sessions/{id}`` fetch in the
    // background — the chat pane renders immediately on
    // the messages, and the header updates once the title
    // round-trip finishes. Empty header in the meantime
    // is fine (the section already has a sensible default).
    void refreshTitle(id, data.messages);
  }

  // Best-effort title fetch — the paginated messages endpoint
  // doesn't carry the session header, so we hit the legacy
  // ``GET /api/chat/sessions/{id}`` for the title + preview.
  // Non-fatal if it 404s (the messages endpoint already
  // 404'd above and we bailed).
  async function refreshTitle(id: string, messagesFromFirstPage: Array<{ role: string; text: string }>) {
    try {
      const r = await fetch(`/api/chat/sessions/${id}`, { credentials: "include" });
      if (!r.ok) return;
      const data = (await r.json()) as { title: string | null };
      setActiveTitle(data.title ?? null);
      // Preview is the first user message — derive from the
      // already-loaded page 0 if we don't get it from the
      // server.
      setActivePreview(
        messagesFromFirstPage.find((m) => m.role === "user")?.text ?? null,
      );
    } catch {
      // Network error on the title fetch is non-fatal.
      setActivePreview(
        messagesFromFirstPage.find((m) => m.role === "user")?.text ?? null,
      );
    }
  }

  // D.18+2 — load the next older page of messages.
  //
  // Called by the "加载更早消息" button at the top of the
  // chat pane. Prepends the older page to the existing
  // ``chatMessages`` array (they sort before the newer
  // ones); updates ``loadedCount`` to track how many
  // active rows are now in state. The button stays
  // visible while ``loadedCount < totalActive`` and hides
  // once we hit the end.
  async function loadOlderMessages() {
    if (pagingOlder || loadedCount >= totalActive) return;
    const sid = sessionId;
    if (!sid) return;
    setPagingOlder(true);
    const PAGE_SIZE = 50;
    try {
      const r = await fetch(
        `/api/chat/sessions/${sid}/messages?limit=${PAGE_SIZE}&offset=${loadedCount}`,
        { credentials: "include" },
      );
      if (!r.ok) return;
      const data = (await r.json()) as {
        messages: Array<{ message_id: string; role: string; text: string; ts: string }>;
        total_active: number;
      };
      // Older messages get negative ids so they sort
      // before the existing ones (which start at 0 and
      // grow upward). This also gives the React list a
      // stable key without ``message_id`` collisions
      // (each (session_id, message_id) is unique but
      // they overlap on UI key uniqueness only when the
      // same message appears in two pages — which it
      // doesn't because the offsets are disjoint).
      const older = data.messages.map((m, i) => ({
        id: -(loadedCount + i + 1),
        role: m.role as "user" | "assistant",
        text: m.text,
      }));
      setChatMessages((prev) => [...older, ...prev]);
      setLoadedCount((n) => n + data.messages.length);
      setTotalActive(data.total_active);
    } finally {
      setPagingOlder(false);
    }
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
      // D.8: if the active session is in the refreshed list
      // and the server now has a title for it (e.g. the
      // auto-title worker fired after our last send), mirror
      // it into ``activeTitle`` so the pane header updates
      // without requiring a manual row click. ``activeTitle``
      // is intentionally NOT cleared when the title is null —
      // a transient worker delay should not blank the header.
      if (sessionId) {
        const active = data.items.find((x) => x.session_id === sessionId);
        if (active && active.title) {
          setActiveTitle(active.title);
        }
      }
    } finally {
      setHistoryLoading(false);
    }
  }

  // (D.18+3 — see ``newChat`` below. The previous behaviour
  // eagerly POSTed ``/api/chat/sessions`` so the sidebar
  // showed a fresh row right away. That filled the sidebar
  // with empty rows when the operator clicked the row,
  // changed their mind, and never sent a message. We now
  // keep the action purely client-side; the session is
  // minted by the first /send.)
  // D.18+3: clicking the sidebar "+ 新对话" row is a **pure
  // UI** action — clear local state, drop the persisted
  // session id, switch to the empty chat pane. No network
  // call. The session row only lands in SQLite (and shows
  // up in the sidebar history) when the operator actually
  // hits Send on the first message.
  //
  // Rationale: the previous version eagerly POSTed to
  // ``/api/chat/sessions`` so a fresh empty row appeared
  // in the sidebar right away. An operator who clicked the
  // row, then changed their mind and never sent anything,
  // left an empty session in the sidebar forever — lots of
  // noise. The backend's ``POST /api/chat/send`` already
  // auto-creates when ``session_id`` is missing, so we
  // just defer the session creation to that path.
  //
  // No ``newChatInflight`` ref needed any more — there's no
  // race against a POST. A rapid double-click just clears
  // state twice, which is idempotent.
  function newChat() {
    setSessionId(null);
    localStorage.removeItem(SESSION_STORAGE_KEY);
    setChatMessages([]);
    setChatInput("");
    setChatError(null);
    setActiveTitle(null);
    setActivePreview(null);
    // Belt-and-braces: if the operator clicked "+ 新对话"
    // from a different tab (Action Items, Settings), make
    // sure the right pane is the conversation view.
    setSelectedId("new-chat");
  }

  async function openSession(id: string) {
    await loadSession(id);
    setSelectedId("new-chat");
    void refreshHistory();
  }

  // D.7 — manual rename commit handler.
  // ``raw`` comes from the inline ``<input>``. Returns early
  // on empty / unchanged to mirror the server's "absent =
  // no touch" semantics — the operator editing then
  // pressing Escape should leave the session alone, not
  // wipe the title.
  async function commitRename(id: string, raw: string) {
    if (editing === null || editing.id !== id) return;
    const trimmed = raw.trim();

    // Build a snapshot of the previous title so we can
    // revert on error.
    const previous =
      history.find((h) => h.session_id === id)?.title ?? null;

    // Close the input regardless of whether we issue the
    // request — the operator has stopped typing.
    setEditing(null);

    if (trimmed === "" || trimmed === previous) {
      // No-op: don't round-trip a request that won't change
      // anything.
      return;
    }

    // Optimistic update — flips the row's label in the
    // sidebar immediately. Reverts if the PATCH fails.
    setHistory((h) =>
      h.map((row) =>
        row.session_id === id ? { ...row, title: trimmed } : row,
      ),
    );
    // D.8: also flip the pane header if this is the active
    // session — same precedence as the sidebar.
    if (id === sessionId) {
      setActiveTitle(trimmed);
    }

    try {
      const r = await fetch(`/api/chat/sessions/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: trimmed }),
        credentials: "include",
      });
      if (!r.ok) {
        setHistory((h) =>
          h.map((row) =>
            row.session_id === id ? { ...row, title: previous } : row,
          ),
        );
        // D.8: matching revert for the pane header.
        if (id === sessionId) {
          setActiveTitle(previous);
        }
        const body = (await r.json().catch(() => ({}))) as {
          code?: string;
          detail?: string;
        };
        setChatError({
          code: body.code ?? "rename_failed",
          detail: body.detail ?? `Rename failed (${r.status})`,
        });
      }
    } catch (err) {
      setHistory((h) =>
        h.map((row) =>
          row.session_id === id ? { ...row, title: previous } : row,
        ),
      );
      setChatError({
        code: "network",
        detail:
          err instanceof Error ? err.message : "Network error",
      });
    }
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
        // D.8: first send of a fresh thread — no title yet
        // (the auto-title worker hasn't run), so seed the
        // pane header with the user message text as preview.
        setActiveTitle(null);
        setActivePreview(text);
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

  // D.9: Sidebar item click is intercepted so the
  // ``+ 新对话`` row actually *creates* a fresh session
  // (rather than just opening an empty pane that lazily
  // creates on next send). Selecting any other row keeps
  // the old behaviour of just switching the right pane.
  function handleSidebarSelect(id: string) {
    if (id === "new-chat") {
      void newChat();
      // Don't ``setSelectedId`` — ``newChat`` is async and
      // sets ``sessionId`` itself, which triggers the same
      // nav state via the conditional render below. If the
      // user clicks again while the POST is in flight the
      // guard at the top of ``newChat`` handles it.
      return;
    }
    setSelectedId(id);
  }

  return (
    <SidebarShell
      items={[...CHAT_CATEGORIES, ...CHAT_ACTIONS]}
      selectedId={selectedId}
      onSelect={handleSidebarSelect}
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
                  {editing?.id === h.session_id ? (
                    // D.7 — inline edit mode. Enter or blur
                    // commits via ``commitRename``; Escape
                    // cancels. ``stopPropagation`` keeps the
                    // click from bubbling to a future
                    // list-level click handler.
                    <input
                      autoFocus
                      value={editing.value}
                      maxLength={80}
                      onChange={(e) =>
                        setEditing((prev) =>
                          prev && prev.id === h.session_id
                            ? { ...prev, value: e.target.value }
                            : prev,
                        )
                      }
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          void commitRename(h.session_id, editing.value);
                        } else if (e.key === "Escape") {
                          e.preventDefault();
                          setEditing(null);
                        }
                      }}
                      onBlur={() => {
                        if (editing && editing.id === h.session_id) {
                          void commitRename(h.session_id, editing.value);
                        }
                      }}
                      onClick={(e) => e.stopPropagation()}
                      className="form-input flex-1 text-xs py-1 px-2"
                    />
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => openSession(h.session_id)}
                        className="flex-1 text-left px-3 py-1.5 text-xs truncate"
                        title={h.title ?? h.preview ?? "(空对话)"}
                      >
                        {(h.title ?? h.preview) || "(空对话)"}{" "}
                        <span
                          className={
                            h.session_id === sessionId
                              ? "opacity-70"
                              : "opacity-60"
                          }
                        >
                          · {h.message_count}条
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditing({
                            id: h.session_id,
                            value: h.title ?? h.preview ?? "",
                          });
                        }}
                        className={
                          "px-2 py-1.5 text-xs " +
                          (h.session_id === sessionId
                            ? "text-white/80 hover:text-white"
                            : "text-ocean/60 hover:text-sky-deep")
                        }
                        title="重命名"
                        aria-label="重命名对话"
                      >
                        ✎
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
                    </>
                  )}
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
          title={activeTitle}
          preview={activePreview}
          hasMoreOlder={loadedCount < totalActive && totalActive > 0}
          totalActive={totalActive}
          loadingOlder={pagingOlder}
          onLoadOlder={loadOlderMessages}
        />
      ) : selectedId === "action-items" ? (
        <ActionItemsPane />
      ) : selectedId === "scheduled-tasks" ? (
        <TaskListPane />
      ) : selectedId === "search" || selectedId === "view-all" ? (
        <ChatSearchPane onOpen={openSession} />
      ) : selected.pane ? (
        <div className="p-8 text-center flex flex-col items-center justify-center">
          {/* pane.title and pane.hint may be raw strings or
              i18n keys (dotted). Translate when keyed;
              otherwise pass through. */}
          <h2 className="text-lg font-semibold text-ink">
            {selected.pane.title.includes(".")
              ? t(selected.pane.title)
              : selected.pane.title}
          </h2>
          <p className="mt-2 text-sm text-ink-soft max-w-md">
            {selected.pane.hint.includes(".")
              ? t(selected.pane.hint)
              : selected.pane.hint}
          </p>
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

