/**
 * ChatSearchPane — full-text search + recent-history browse.
 *
 * Two modes, switched on the search input:
 *
 *  - **Search mode** (query non-empty). Debounces user
 *    input (300 ms) and calls
 *    ``GET /api/chat/search?q=...&limit=20`` for FTS5
 *    matches. Renders one row per hit with a
 *    ``<mark>``-highlighted snippet + the conversation title +
 *    role tag.
 *
 *  - **Browse mode** (query empty). Calls
 *    ``GET /api/chat/conversations?limit=N&offset=M`` and renders
 *    the most-recently-updated conversations as a
 *    "latest conversations" list. Infinite-scroll via an
 *    IntersectionObserver on a sentinel ``<div>`` at the
 *    bottom: when it scrolls into view, fetch the next
 *    page. ``updated_at`` desc sort gives the operator a
 *    chronological conversation log without doing any
 *    thinking.
 *
 * Both modes use the same row layout (so an operator
 * transitioning from "no search" → "search" sees a
 * consistent visual) and the same ``onOpen`` callback —
 * row click → ``openConversation(id)`` → chat pane.
 *
 * Auth + scope:
 *   The cookie-based admin gate is handled upstream by the
 *   HTTP route. The search route scopes by ``uid``
 *   (D.18+1); the conversations list route scopes by
 *   ``delivery_address`` (the per-channel delivery address
 *   column — D.28 renamed the legacy ``tgid`` column).
 *   Both end up showing the operator's own history — the
 *   two routes just have different SQL keys because the
 *   underlying data models are different. We don't
 *   cross-check them here; the cookie-bound endpoint is
 *   the source of truth.
 *
 * The frontend never tries to ``unmark`` the snippets; the
 * backend ships them with ``<mark>...</mark>`` already in
 * place. The default browser style for ``<mark>`` is loud
 * yellow; the project's ``styles.css`` tones it down to
 * match the pastel palette.
 */

import { useEffect, useRef, useState } from "react";
import { useT } from "../../i18n/index";
import {
  useChatSearch,
  useChatConversations,
  type ChatConversationList,
  type ChatSearchResult,
} from "../../lib/queries";
import Notice from "../../components/Notice";

// One row of the search result list. Mirrors the
// backend's ``SearchHit`` shape (see
// ``magi.channels.api.chat_search.SearchHit``).
// Lives here as a named alias so the row component
// doesn't have to thread the parent type through.
type SearchHit = ChatSearchResult["items"][number];
type ConversationListItem = ChatConversationList["items"][number];

type Props = {
  /** Deep-link into the matching thread. Matches
   *  DashboardPage's ``openConversation`` helper. */
  onOpen: (conversationId: number) => void;
};

const DEBOUNCE_MS = 300;
// Browse-mode page size: small enough to render fast on
// the first paint, large enough that two pages cover the
// typical "recent activity" view without an extra fetch.
const BROWSE_PAGE = 20;

export default function ChatSearchPane({ onOpen }: Props) {
  const t = useT();
  const [query, setQuery] = useState("");
  // Debounced mirror of ``query`` — the value fed to the
  // search hook. ``useChatSearch`` is keyed by this
  // string so a re-typed query hits the cache; a rapid
  // keystroke sequence fires the network only once after
  // the operator pauses.
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const searchQuery = useChatSearch(debouncedQuery);

  // Browse-mode state. We accumulate pages locally so
  // an infinite scroll doesn't drop earlier entries
  // when react-query re-fetches a new page (each page
  // is its own cache entry, not a replace).
  const [browseItems, setBrowseItems] = useState<ConversationListItem[]>([]);
  const [browseTotal, setBrowseTotal] = useState(0);
  const [browseOffset, setBrowseOffset] = useState(0);
  const [browseExhausted, setBrowseExhausted] = useState(false);

  // Sentinel ref for infinite scroll: an IntersectionObserver
  // watches this div; when it scrolls into view we fetch
  // the next page.
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // Browse-mode page hook. Always enabled (browse mode
  // is the default when the search box is empty). The
  // query is keyed by ``(limit, offset)`` so each page
  // is its own cache entry — perfect for infinite scroll.
  const browseQuery = useChatConversations({
    limit: BROWSE_PAGE,
    offset: browseOffset,
  });

  // ────────────────────────────────────────────────────────
  // Debounce: keep the in-flight search hook input
  // stable for 300 ms after the last keystroke.
  // ────────────────────────────────────────────────────────
  const debounceRef = useRef<number | null>(null);
  useEffect(() => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      setDebouncedQuery(query);
    }, DEBOUNCE_MS);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, [query]);

  // ────────────────────────────────────────────────────────
  // Browse-mode page accumulation
  // ────────────────────────────────────────────────────────
  //
  // When the search box is empty and we're not in the
  // first-paint offset, fold the freshly-fetched page
  // into the accumulated list. The first page replaces
  // the list (handles the "search cleared" reset).
  const isFirstBrowsePage = browseOffset === 0;
  useEffect(() => {
    if (query.trim()) return; // search mode owns the result list
    if (!browseQuery.data) return;
    const body: ChatConversationList = browseQuery.data;
    setBrowseTotal(body.total);
    setBrowseExhausted(browseOffset + body.items.length >= body.total);
    setBrowseItems((prev) =>
      isFirstBrowsePage ? body.items : [...prev, ...body.items],
    );
  }, [browseQuery.data, browseOffset, isFirstBrowsePage, query]);

  // Reset browse on query-clear so the recent-history
  // list comes back when the operator types-then-deletes.
  useEffect(() => {
    if (query.trim()) return;
    setBrowseItems([]);
    setBrowseTotal(0);
    setBrowseOffset(0);
    setBrowseExhausted(false);
    // The hook will refetch on its own once ``offset`` is
    // back to 0 (cache key changes).
  }, [query]);

  // Infinite-scroll observer. Fires when the sentinel
  // enters the viewport, requesting the next page if we
  // haven't already reached the total.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          if (query.trim()) return; // search mode
          if (browseQuery.isFetching) return;
          if (browseExhausted) return;
          setBrowseOffset((prev) => prev + BROWSE_PAGE);
        }
      },
      // 200px root margin so the next page loads slightly
      // before the operator reaches the absolute bottom —
      // no awkward pause at the end of the scroll.
      { rootMargin: "200px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [query, browseQuery.isFetching, browseExhausted]);

  // ────────────────────────────────────────────────────────
  // Render
  // ────────────────────────────────────────────────────────

  const inSearchMode = query.trim().length > 0;
  const searchData = searchQuery.data as ChatSearchResult | undefined;
  const searchLoading = searchQuery.isFetching;
  const searchError = (() => {
    if (!searchQuery.error) return null;
    const err = searchQuery.error as { status?: number; message?: string };
    if (err.status === 503) {
      return err.message ?? "Search is not available in this build (FTS5 missing)";
    }
    return err.message ?? "Network error";
  })();
  const browseError = (browseQuery.error as Error | null)?.message ?? null;

  return (
    // ``h-full`` lets us inherit SidebarShell's column
    // height (the shell's outer card is ``h-[calc(100vh-7rem)]``).
    // Previously this was a hard-coded ``h-[560px]`` which
    // left the bottom of the panel empty on viewports
    // taller than ~700 px, and overflowed the sidebar on
    // shorter viewports. Matching the chat / task panes'
    // pattern keeps the layout consistent across tabs.
    <div className="flex flex-col h-full min-h-0">
      <div className="px-6 py-3 border-b border-border">
        <h2 className="text-base font-semibold text-ink">{t("chatSearch.title")}</h2>
        <p className="mt-1 text-xs text-ink-soft">
          {inSearchMode ? t("chatSearch.emptyHintSearch") : t("chatSearch.emptyHintBrowse")}
        </p>
      </div>

      <div className="px-6 py-3 border-b border-border">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("chatSearch.searchPlaceholder")}
          autoFocus
          aria-label={t("chatSearch.searchAria")}
          className="w-full px-3 py-2 rounded-md border border-border bg-surface text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
        />
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4">
        {/* ── Search mode ───────────────────────────────── */}
        {inSearchMode && (
          <>
            {searchLoading && (
              <p className="text-sm text-ink-soft text-center mt-6">
                {t("chatSearch.searching")}
              </p>
            )}
            {!searchLoading && searchError && (
              <div className="mt-12">
                <Notice tone="warning">{searchError}</Notice>
              </div>
            )}
            {!searchLoading && !searchError && searchData && searchData.items.length === 0 && (
              <p className="text-sm text-ink-soft text-center mt-6">
                {t("chatSearch.noMatch")}
              </p>
            )}
            {!searchLoading && !searchError && searchData && searchData.items.length > 0 && (
              <ul className="flex flex-col gap-3">
                {searchData.items.map((h) => (
                  <SearchHitRow
                    key={`${h.conversation_id}:${h.message_id}`}
                    hit={h}
                    onOpen={onOpen}
                  />
                ))}
              </ul>
            )}
          </>
        )}

        {/* ── Browse mode ──────────────────────────────── */}
        {!inSearchMode && (
          <>
            {browseError && (
              <div className="mb-4">
                <Notice tone="warning">{browseError}</Notice>
              </div>
            )}

            {browseItems.length === 0 && !browseQuery.isFetching && !browseError && (
              <p className="text-sm text-ink-soft text-center mt-6">
                {t("chatSearch.emptyBrowse")}
              </p>
            )}

            {browseItems.length > 0 && (
              <ul className="flex flex-col gap-3">
                {browseItems.map((s) => (
                  <ConversationListItemRow
                    key={s.conversation_id}
                    conversation={s}
                    onOpen={onOpen}
                  />
                ))}
              </ul>
            )}

            {/* The infinite-scroll sentinel. Lives inside the
                scroll container; the observer above triggers
                ``loadBrowsePage`` when it enters view. We
                hide it once we've exhausted the total so
                the operator doesn't see a permanently
                pulsing "loading…" at the bottom. */}
            {!browseExhausted && (
              <div
                ref={sentinelRef}
                className="py-4 text-center text-xs text-ink-soft"
              >
                {browseQuery.isFetching ? t("chatSearch.loadMore") : ""}
              </div>
            )}
            {browseExhausted && browseItems.length > 0 && (
              <p className="py-4 text-center text-xs text-ink-soft">
                {t("chatSearch.endOfList").replace("{total}", String(browseTotal))}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────
// Row components
// ────────────────────────────────────────────────────────

function SearchHitRow({
  hit,
  onOpen,
}: {
  hit: SearchHit;
  onOpen: (conversationId: number) => void;
}) {
  const t = useT();
  return (
    <li
      className="rounded-lg border border-border bg-surface hover:bg-surface-2 transition cursor-pointer"
      onClick={() => onOpen(hit.conversation_id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(hit.conversation_id);
        }
      }}
    >
      <div className="p-3">
        <div className="flex items-center justify-between gap-2 mb-1">
          <h3 className="text-sm font-medium text-ink truncate">
            {hit.title ?? String(hit.conversation_id).slice(0, 13) + "…"}
          </h3>
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-ink-soft border border-border rounded px-1.5 py-0.5">
            {hit.role === "user"
              ? t("chatSearch.roleUser")
              : hit.role === "assistant"
                ? t("chatSearch.roleAssistant")
                : t("chatSearch.roleSystem")}
          </span>
        </div>
        <p
          className="text-xs text-ink-soft leading-relaxed"
          dangerouslySetInnerHTML={{ __html: hit.snippet }}
        />
        <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-ink-soft">
          <span className="truncate font-mono">{hit.conversation_id}</span>
          <span>{formatTime(hit.ts)}</span>
        </div>
      </div>
    </li>
  );
}

function ConversationListItemRow({
  conversation,
  onOpen,
}: {
  conversation: ConversationListItem;
  onOpen: (conversationId: number) => void;
}) {
  const displayTitle = conversation.title ?? "(无标题对话)";
  return (
    <li
      className="rounded-lg border border-border bg-surface hover:bg-surface-2 transition cursor-pointer"
      onClick={() => onOpen(conversation.conversation_id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(conversation.conversation_id);
        }
      }}
    >
      <div className="p-3">
        <h3 className="text-sm font-medium text-ink truncate">
          {displayTitle}
        </h3>
        <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-ink-soft">
          <span className="truncate font-mono">
            {String(conversation.conversation_id).slice(0, 13) + "…"}
          </span>
          <span>{formatRelative(conversation.updated_at)}</span>
        </div>
      </div>
    </li>
  );
}

// ────────────────────────────────────────────────────────
// Time formatters
// ────────────────────────────────────────────────────────

function formatTime(iso: string): string {
  // Absolute: "2026-07-05 14:32". Used by the search-mode
  // row, where the matched message's timestamp is the
  // primary time signal.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

function formatRelative(iso: string): string {
  // Relative: "3 天前" / "刚刚". Used by the browse-mode
  // row, where "how recent is this conversation" is the
  // primary time signal. Anchored to ``Date.now()`` on the
  // client (close enough for v0; the backend's
  // ``updated_at`` is server-stamped to UTC ms so skew is
  // bounded by the operator's local clock vs UTC).
  const past = new Date(iso).getTime();
  const now = Date.now();
  if (Number.isNaN(past)) return iso;
  const deltaSec = Math.max(0, Math.round((now - past) / 1000));
  if (deltaSec < 60) return "刚刚";
  const mins = Math.round(deltaSec / 60);
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} 天前`;
  // Older than a month: drop to absolute date so the
  // "9 个月前"-style output doesn't get unwieldy.
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
