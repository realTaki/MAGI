/**
 * ChatTab — chat session list + conversation pane.
 *
 * Two-column shell: sidebar on the left (6 EVA-output
 * categories at the top, then a separator, then
 * 新对话 / 搜索对话 / 历史对话 with a top-20 list
 * and a 查看全部 affordance), and a content pane on
 * the right that changes based on what's selected.
 *
 * SidebarItem.label convention in this file: dotted
 * i18n keys (`sidebar.actionItems`, `sidebar.meetings`).
 * The shell passes them through verbatim. (MAGI Council /
 * Knowledge use raw Chinese; Settings resolves keys in
 * the renderer — see plan TODO.)
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, qk } from "../lib/queryClient";



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
// Two-column shell: a sidebar on the left (6 EVA-output categories
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
 *  ``GET /api/chat/conversations?limit=50`` now; the cap of 20 is
 *  purely a UI cap (the sidebar shows the first 20 with a
 *  "load more" expansion when the server has more). */
const HISTORY_VISIBLE_LIMIT = 20;

/** Storage key for the active chat session id. We keep the
 *  *just-opened* session in localStorage so a hard refresh
 *  restores the live thread. The backend is the source of
 *  truth — localStorage is just a "last known" pointer. */
const CONVERSATION_STORAGE_KEY = "magi_chat_conversation_id";

/** A persisted conversation in the ``/api/chat/conversations`` list response. */
type ConversationListItem = {
  conversation_id: number;
  created_at: string;
  updated_at: string;
  title: string | null;
  channel: string;
};

export default function ChatTab() {
  // "view-all" is a synthetic id that aliases the search view (per
  // the design — clicking the last row in the history list should
  // behave like opening search).
  const t = useT();
  const [selectedId, setSelectedId] = useState<string>(CHAT_CATEGORIES[0].id);

  // -- conversation lifecycle (D.6) -------------------------------
  // ``conversationId`` is the file-backed chat thread the operator
  // currently has open. ``null`` means "no conversation yet" (the
  // next /send call will auto-create one); the server
  // returns the new id in the response.
  const [conversationId, setConversationId] = useState<number | null>(() => {
    const raw = localStorage.getItem(CONVERSATION_STORAGE_KEY);
    return raw ? Number(raw) || null : null;
  });
  // History list — most recent first, scoped to the
  // current tgid (server resolves via cookie).
  const [history, setHistory] = useState<ConversationListItem[]>([]);
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
    { id: number; value: string } | null
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
  const [chatPhase, setChatPhase] = useState<"idle" | "sending" | "typing">("idle");
  const [assistantCountAtSend, setAssistantCountAtSend] = useState(0);
  const chatSending = chatPhase !== "idle";
  // ``chatError`` carries the stable backend error ``code`` so
  // the renderer can pick a friendlier message than the English
  // ``detail`` for known cases (e.g. ``chat.llm_credentials_required``
  // points the operator at the MAGI Council tab where their
  // per-contact LLM is configured). Unknown codes fall through
  // to ``detail`` so a missing translation never blanks the UI.
  const [chatError, setChatError] = useState<
    { code: string; detail: string } | null
  >(null);

  // D.8: pane header follows the active session.
  const [activeTitle, setActiveTitle] = useState<string | null>(null);

  // -- helpers -----------------------------------------------------

  // First page of the active session. Auto-refetched on focus.
  const PAGE_SIZE = 50;
  const messagesQuery = useQuery({
    queryKey: conversationId ? [...qk.chatMessages(conversationId), "page-0"] : ["chatMessages", "none", "page-0"],
    queryFn: () =>
      apiFetch<{
        conversation_id: number;
        messages: Array<{ message_id: number; role: string; text: string; ts: string }>;
        total_active: number;
      }>(`/api/chat/conversations/${conversationId as number}/messages?limit=${PAGE_SIZE}&offset=0`),
    enabled: conversationId !== null,
    refetchOnWindowFocus: true,
  });
  // Mirror messagesQuery into chatMessages / loadedCount / totalActive
  // / conversationId. Side effects (404 → clear localStorage, error
  // envelope) are derived from the query status and applied via
  // a single useEffect. The legacy callers still mutate
  // ``chatMessages`` directly (loadOlderMessages, sendChat), so the
  // mirror has to keep its functional-setter pattern.
  useEffect(() => {
    if (conversationId === null) {
      setChatMessages([]);
      setLoadedCount(0);
      setTotalActive(0);
      return;
    }
    if (messagesQuery.isError) {
      const e = messagesQuery.error as { status?: number } | null;
      if (e?.status === 404) {
        localStorage.removeItem(CONVERSATION_STORAGE_KEY);
        setConversationId(null);
        setChatMessages([]);
        setActiveTitle(null);
        setHistory((h) => h.filter((x) => x.conversation_id !== conversationId));
        return;
      }
      const detail = e instanceof Error ? e.message : `Load failed`;
      setChatError({ code: "unknown", detail });
      return;
    }
    if (!messagesQuery.data) return;
    const data = messagesQuery.data;
    // Persist the (possibly server-renamed) session id.
    if (data.conversation_id !== conversationId) {
      setConversationId(data.conversation_id);
    }
    localStorage.setItem(CONVERSATION_STORAGE_KEY, String(data.conversation_id));
    setChatMessages((prev) => {
      // Merge server data with any optimistic messages that
      // haven't been confirmed yet. ``sendChat`` adds the
      // user row at T=0 with ``id: Date.now()`` so it shows
      // up immediately; the first ``/messages`` refetch can
      // race the chat_messages commit and return without
      // the user row, which previously wiped the optimistic
      // entry. We dedupe by ``role + text`` fingerprint (not
      // id): when the server catches up, the matching
      // optimistic row is dropped instead of being shown
      // alongside the server copy.
      const serverFingerprints = new Set(
        data.messages.map((m) => `${m.role}::${m.text}`),
      );
      const pendingOptimistic = prev.filter(
        (m) => !serverFingerprints.has(`${m.role}::${m.text}`),
      );
      const serverMsgs = data.messages.map((m) => ({
        id: m.message_id,
        role: m.role as "user" | "assistant",
        text: m.text,
      }));
      return [...serverMsgs, ...pendingOptimistic];
    });
    setLoadedCount(data.messages.length);
    // ``totalActive`` is server-authoritative for committed
    // rows; optimistic rows aren't counted yet (the
    // operator sees them, the count catches up on the next
    // refetch). Including them here would briefly inflate
    // "load older" affordances that don't apply.
    setTotalActive(data.total_active);
    if (
      chatPhase === "typing" &&
      data.messages.filter((message) => message.role === "assistant").length > assistantCountAtSend
    ) {
      setChatPhase("idle");
    }
    setChatError(null);
  }, [conversationId, messagesQuery.data, messagesQuery.isError, messagesQuery.error, chatPhase, assistantCountAtSend]);

  // A claimed turn can outlive the short bootstrap invalidations below.
  // Keep polling the transcript while the Agent owns it, then stop as soon
  // as the assistant row arrives (the effect above switches us to idle).
  useEffect(() => {
    if (chatPhase !== "typing" || conversationId === null) return;
    const interval = window.setInterval(() => {
      void messagesQuery.refetch();
    }, 2_000);
    return () => window.clearInterval(interval);
  }, [chatPhase, conversationId, messagesQuery.refetch]);

  function loadSession(id: number) {
    setChatPhase("idle");
    setChatError(null);
    setLoadedCount(0);
    setTotalActive(0);
    setConversationId(id);
    localStorage.setItem(CONVERSATION_STORAGE_KEY, String(id));
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
    const sid = conversationId;
    if (!sid) return;
    setPagingOlder(true);
    const PAGE_SIZE = 50;
    try {
      const r = await fetch(
        `/api/chat/conversations/${sid}/messages?limit=${PAGE_SIZE}&offset=${loadedCount}`,
        { credentials: "include" },
      );
      if (!r.ok) return;
      const data = (await r.json()) as {
        messages: Array<{ message_id: number; role: string; text: string; ts: string }>;
        total_active: number;
      };
      // Older messages get negative ids so they sort
      // before the existing ones (which start at 0 and
      // grow upward). This also gives the React list a
      // stable key without ``message_id`` collisions
      // (each (conversation_id, message_id) is unique but
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

  const historyQuery = useQuery({
    queryKey: qk.chatConversations(),
    queryFn: () =>
      apiFetch<{ items: ConversationListItem[]; total: number }>(
        `/api/chat/conversations?limit=${historyLimit}&offset=0`,
      ),
    refetchOnWindowFocus: true,
  });
  // Mirror the query result into local state. The legacy hooks
  // (``setHistory``/``setHistoryTotal``) are still consumed by
  // the sidebar list, so we keep them in sync rather than
  // rewriting every consumer.
  useEffect(() => {
    if (historyQuery.data) {
      setHistory(historyQuery.data.items);
      setHistoryTotal(historyQuery.data.total);
      if (conversationId) {
        const active = historyQuery.data.items.find(
          (x: ConversationListItem) => x.conversation_id === conversationId,
        );
        if (active && active.title) {
          setActiveTitle(active.title);
        }
      }
    }
  }, [historyQuery.data, conversationId]);
  // ``historyLoading`` is kept in sync for the pull-to-refresh
  // spinner.
  useEffect(() => {
    setHistoryLoading(historyQuery.isFetching);
  }, [historyQuery.isFetching]);
  function refreshHistory() {
    void historyQuery.refetch();
  }
  // Session header title. Auto-refetches on focus.
  const sessionTitleQuery = useQuery({
    queryKey: conversationId ? qk.chatMessages(conversationId) : ["chatConversation", "none"],
    queryFn: () =>
      apiFetch<{ title: string | null }>(`/api/chat/conversations/${conversationId as number}`),
    enabled: conversationId !== null,
    refetchOnWindowFocus: true,
  });
  useEffect(() => {
    if (conversationId === null) {
      setActiveTitle(null);
      return;
    }
    if (sessionTitleQuery.data && sessionTitleQuery.data.title) {
      setActiveTitle(sessionTitleQuery.data.title);
    }
  }, [conversationId, sessionTitleQuery.data]);

  // (D.18+3 — see ``newChat`` below. The previous behaviour
  // eagerly POSTed ``/api/chat/conversations`` so the sidebar
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
  // ``/api/chat/conversations`` so a fresh empty row appeared
  // in the sidebar right away. An operator who clicked the
  // row, then changed their mind and never sent anything,
  // left an empty session in the sidebar forever — lots of
  // noise. The backend's ``POST /api/chat/send`` already
  // auto-creates when ``conversation_id`` is missing, so we
  // just defer the session creation to that path.
  //
  // No ``newChatInflight`` ref needed any more — there's no
  // race against a POST. A rapid double-click just clears
  // state twice, which is idempotent.
  function newChat() {
    setChatPhase("idle");
    setConversationId(null);
    localStorage.removeItem(CONVERSATION_STORAGE_KEY);
    setChatMessages([]);
    setChatInput("");
    setChatError(null);
    setActiveTitle(null);
    // Belt-and-braces: if the operator clicked "+ 新对话"
    // from a different tab (Action Items, Settings), make
    // sure the right pane is the conversation view.
    setSelectedId("new-chat");
  }

  async function openSession(id: number) {
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
  async function commitRename(id: number, raw: string) {
    if (editing === null || editing.id !== id) return;
    const trimmed = raw.trim();

    // Build a snapshot of the previous title so we can
    // revert on error.
    const previous =
      history.find((h) => h.conversation_id === id)?.title ?? null;

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
        row.conversation_id === id ? { ...row, title: trimmed } : row,
      ),
    );
    // D.8: also flip the pane header if this is the active
    // session — same precedence as the sidebar.
    if (id === conversationId) {
      setActiveTitle(trimmed);
    }

    try {
      const r = await fetch(`/api/chat/conversations/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: trimmed }),
        credentials: "include",
      });
      if (!r.ok) {
        setHistory((h) =>
          h.map((row) =>
            row.conversation_id === id ? { ...row, title: previous } : row,
          ),
        );
        // D.8: matching revert for the pane header.
        if (id === conversationId) {
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
          row.conversation_id === id ? { ...row, title: previous } : row,
        ),
      );
      setChatError({
        code: "network",
        detail:
          err instanceof Error ? err.message : "Network error",
      });
    }
  }

  async function deleteSession(id: number) {
    if (!confirm("删除这条对话？")) return;
    const r = await fetch(`/api/chat/conversations/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (r.ok || r.status === 404) {
      // Filter locally; ignore server states (the route is
      // idempotent, so 200 / 204 / 404 all mean "gone").
      setHistory((h) => h.filter((x) => x.conversation_id !== id));
      setHistoryTotal((t) => Math.max(0, t - 1));
      // If we just deleted the active session, drop the
      // localStorage pointer and start fresh — the next
      // /send will auto-create.
      if (id === conversationId) {
        setChatPhase("idle");
        localStorage.removeItem(CONVERSATION_STORAGE_KEY);
        setConversationId(null);
        setChatMessages([]);
      }
    }
  }

  // -- mount effects -----------------------------------------------

  // On first mount, hydrate the active session from
  // localStorage. If the id no longer exists, ``loadSession``
  // drops the pointer and starts clean.
  useEffect(() => {
    const id = Number(localStorage.getItem(CONVERSATION_STORAGE_KEY));
    if (id) loadSession(id);
    void refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const queryClientForChat = useQueryClient();
  const sendChatMut = useMutation({
    mutationFn: (vars: { text: string; conversationId: number | null }) =>
      apiFetch<{ job_id: number; status: string; conversation_id: number }>("/api/chat/send", {
        method: "POST",
        body: { text: vars.text, conversation_id: vars.conversationId },
      }),
    onSuccess: (data: { job_id: number; status: string; conversation_id: number }, vars: { text: string; conversationId: number | null }) => {
      if (data.conversation_id !== vars.conversationId) {
        setConversationId(data.conversation_id);
        localStorage.setItem(CONVERSATION_STORAGE_KEY, String(data.conversation_id));
        setActiveTitle(null);
        void refreshHistory();
      }
      // The durable turn runs asynchronously.  The first refresh picks up
      // the committed user row; retries pick up a fast failure (such as an
      // unconfigured provider) or the eventual assistant delivery without
      // requiring the operator to switch tabs or manually reload.
      // There is currently no browser-facing SSE stream for this board, so
      // use a short, bounded revalidation sequence instead. The chat pane's
      // mirror effect dedupes optimistic messages against server rows by
      // ``role + text`` fingerprint, so a race that returns the user row
      // late no longer wipes the optimistic placeholder.
      const transcriptKey = qk.chatMessages(data.conversation_id);
      for (const delayMs of [500, 1_000, 2_000, 4_000, 8_000]) {
        window.setTimeout(() => {
          void queryClientForChat.invalidateQueries({ queryKey: transcriptKey });
        }, delayMs);
      }
      void waitForAgentReceipt(data.job_id);
    },
    onError: (err: unknown): void => {
      setChatPhase("idle");
      if (err && typeof err === "object" && "status" in err) {
        const e = err as { status?: number; message?: string };
        if (e.status && e.status >= 400 && e.status < 500) {
          setChatError({
            code: "unknown",
            detail: e.message ?? `Send failed (${e.status})`,
          });
          return;
        }
      }
      setChatError({
        code: "network",
        detail: err instanceof Error ? err.message : "Network error",
      });
    },
  });

  async function waitForAgentReceipt(jobId: number) {
    // PROCESSING is the durable receipt: the Agent has claimed this turn.
    // COMPLETED/FAILED also imply it was received (a very fast run may pass
    // through PROCESSING between polling ticks).
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        const receipt = await apiFetch<{ job_id: number; status: string }>(
          `/api/chat/notifications/${jobId}`,
        );
        if (receipt.status !== "pending") {
          setChatPhase("typing");
          return;
        }
      } catch (err) {
        setChatError({
          code: "chat.receipt_unavailable",
          detail: err instanceof Error ? err.message : "Could not confirm Agent receipt",
        });
        setChatPhase("idle");
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    setChatError({
      code: "chat.receipt_timeout",
      detail: "Agent has not received this message yet.",
    });
    setChatPhase("idle");
  }

  async function sendChat() {
    const text = chatInput.trim();
    if (!text || chatSending) return;
    setChatInput("");
    setChatError(null);
    const userMsg = { id: Date.now(), role: "user" as const, text };
    setChatMessages((prev) => [...prev, userMsg]);
    setAssistantCountAtSend(chatMessages.filter((message) => message.role === "assistant").length);
    setChatPhase("sending");
    try {
      await sendChatMut.mutateAsync({ text, conversationId });
    } catch {
      // onError owns the user-visible error and resets the phase.
    }
  }

  const allById: Record<string, ChatItem> = {};
  for (const c of CHAT_CATEGORIES) allById[c.id] = c;
  for (const a of CHAT_ACTIONS) allById[a.id] = a;
  // ``HISTORY`` (the placeholder) is intentionally not
  // merged in anymore — D.6 replaces it with a real list
  // driven from ``/api/chat/conversations``. The right-pane
  // "view-all" / "search" entry is still synthetic.
  allById["view-all"] = allById["search"];

  const selected = allById[selectedId] ?? CHAT_CATEGORIES[0];
  // The sidebar's "历史对话" list — latest first, the first
  // ``HISTORY_VISIBLE_LIMIT`` of the 50 the server sent. Each
  // row shows its title or an untitled fallback label;
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
      // sets ``conversationId`` itself, which triggers the same
      // nav state via the conditional render below. If the
      // user clicks again while the POST is in flight the
      // guard at the top of ``newChat`` handles it.
      return;
    }
    setSelectedId(id);
  }

  // Cross-channel guard: sessions created on tg/task are
  // visible in WebUI HUB but read-only — the channel that
  // owns the session is the only one that can write to it.
  const currentSummary = history.find((h) => h.conversation_id === conversationId);
  const isReadonly = currentSummary !== undefined && currentSummary.channel !== "webui";
  const readonlyChannel = isReadonly ? currentSummary.channel : null;

  return (
    <SidebarShell
      items={[...CHAT_CATEGORIES, ...CHAT_ACTIONS]}
      selectedId={selectedId}
      onSelect={handleSidebarSelect}
      ariaLabel="Chat navigation"
      belowItems={
        <>
          <hr className="my-3 border-border" />
          <p className="mt-1 mb-1 px-3 text-xs text-ink-3 font-medium">
            历史对话
          </p>
          {historyLoading && history.length === 0 ? (
            <p className="px-3 text-xs text-ink-2">Loading…</p>
          ) : history.length === 0 ? (
            <p className="px-3 text-xs text-ink-2">
              No conversations yet.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {historyVisible.map((h) => {
                const isForeign = h.channel && h.channel !== "webui";
                return (
                <li
                  key={h.conversation_id}
                  className={
                    "flex items-center gap-1 rounded-md transition-colors " +
                    (h.conversation_id === conversationId
                      ? (isForeign ? "bg-surface-2 text-ink-3" : "bg-accent text-white")
                      : (isForeign
                        ? "text-ink-3 hover:bg-surface-2"
                        : "text-ink-2 hover:bg-surface-2 hover:text-ink"))
                  }
                >
                  {editing?.id === h.conversation_id ? (
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
                          prev && prev.id === h.conversation_id
                            ? { ...prev, value: e.target.value }
                            : prev,
                        )
                      }
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          void commitRename(h.conversation_id, editing.value);
                        } else if (e.key === "Escape") {
                          e.preventDefault();
                          setEditing(null);
                        }
                      }}
                      onBlur={() => {
                        if (editing && editing.id === h.conversation_id) {
                          void commitRename(h.conversation_id, editing.value);
                        }
                      }}
                      onClick={(e) => e.stopPropagation()}
                      className="form-input flex-1 text-xs py-1 px-2"
                    />
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => openSession(h.conversation_id)}
                        className="flex-1 text-left px-3 py-1.5 text-xs truncate"
                        title={h.title ?? "(无标题对话)"}
                      >
                        {h.title ?? "(无标题对话)"}
                        {isForeign && (
                          <span className="ml-1 text-[10px] opacity-70">
                            [{h.channel}]
                          </span>
                        )}{" "}
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditing({
                            id: h.conversation_id,
                            value: h.title ?? "",
                          });
                        }}
                        className={
                          "px-2 py-1.5 text-xs " +
                          (h.conversation_id === conversationId
                            ? "text-white/80 hover:text-white"
                            : "text-ink-3 hover:text-ink")
                        }
                        title="重命名"
                        aria-label="重命名对话"
                      >
                        ✎
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteSession(h.conversation_id)}
                        className={
                          "px-2 py-1.5 text-xs " +
                          (h.conversation_id === conversationId
                            ? "text-white/80 hover:text-white"
                            : "text-ink-3 hover:text-ink")
                        }
                        title="删除"
                        aria-label="删除对话"
                      >
                        ✕
                      </button>
                    </>
                  )}
                </li>
              );
            })}
              {historyOverflow > 0 && (
                <button
                  type="button"
                  onClick={() => setHistoryExpanded((b) => !b)}
                  className="mt-1 w-full text-left px-3 py-1.5 text-xs text-accent hover:text-accent-ink"
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
              "mt-1 w-full text-left px-3 py-1.5 rounded-md text-xs transition-colors " +
              (selectedId === "view-all"
                ? "bg-accent text-white"
                : "text-accent hover:text-accent-ink hover:bg-surface-2")
            }
          >
            查看全部 →
          </button>
        </>
      }
    >
      {selectedId === "new-chat" ? (
        <ChatConversationPane
          messages={chatMessages as Array<{ id: number; role: "user" | "assistant"; text: string; ts: string }>}
          input={chatInput}
          onInputChange={setChatInput}
          sending={chatSending}
          sendingLabel={chatPhase === "sending" ? `${t("chat.send")}…` : t("chat.sending")}
          error={chatError}
          onSend={sendChat}
          title={activeTitle}
          hasMoreOlder={loadedCount < totalActive && totalActive > 0}
          totalActive={totalActive}
          loadedCount={loadedCount}
          loadingOlder={pagingOlder}
          onLoadOlder={loadOlderMessages}
          onNewChat={() => setSelectedId("new-chat")}
          readonly={isReadonly}
          channelLabel={readonlyChannel}
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
