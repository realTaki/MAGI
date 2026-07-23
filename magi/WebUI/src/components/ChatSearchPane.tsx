/**
 * ChatSearchPane — full-text search + recent-history browse.
 *
 * Two modes, switched on the search input:
 *
 *  - **Search mode** (query non-empty). Debounces user
 *    input (300 ms) and calls
 *    ``GET /api/chat/search?q=...&limit=20`` for FTS5
 *    matches. Renders one row per hit with a
 *    ``<mark>``-highlighted snippet + the session title +
 *    role tag.
 *
 *  - **Browse mode** (query empty). Calls
 *    ``GET /api/chat/sessions?limit=N&offset=M`` and renders
 *    the most-recently-updated sessions as a
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
 * row click → ``openSession(id)`` → chat pane.
 *
 * Auth + scope:
 *   The cookie-based admin gate is handled upstream by the
 *   HTTP route. The search route scopes by ``uid``
 *   (D.18+1); the sessions list route scopes by ``tgid``.
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

import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "../i18n/index";

type SearchHit = {
  session_id: string;
  message_id: string;
  role: "user" | "assistant" | "system";
  ts: string;
  snippet: string;
  title: string | null;
  score: number;
  tgid: string;
  channel: string;
};

type SearchResponse = {
  q: string;
  uid: number;
  items: SearchHit[];
  total: number;
  limit: number;
  offset: number;
};

type SessionSummary = {
  session_id: string;
  created_at: string;
  created_by_employee_id: number;
  updated_at: string;
  message_count: number;
  preview: string;
  title: string | null;
};

type SessionListResponse = {
  items: SessionSummary[];
  total: number;
  limit: number;
  offset: number;
};

type ApiError = { code?: string; detail?: string };

type Props = {
  /** Deep-link into the matching thread. Matches
   *  DashboardPage's ``openSession`` helper. */
  onOpen: (sessionId: string) => void;
};

const DEBOUNCE_MS = 300;
const SEARCH_LIMIT = 20;
// Browse-mode page size: small enough to render fast on
// the first paint, large enough that two pages cover the
// typical "recent activity" view without an extra fetch.
const BROWSE_PAGE = 20;

export default function ChatSearchPane({ onOpen }: Props) {
  const t = useT();
  const [query, setQuery] = useState("");

  // Search-mode state (existing FTS5 path).
  const [searchData, setSearchData] = useState<SearchResponse | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  // Browse-mode state (paginated recent history).
  const [browseItems, setBrowseItems] = useState<SessionSummary[]>([]);
  const [browseTotal, setBrowseTotal] = useState(0);
  const [browseOffset, setBrowseOffset] = useState(0);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [browseExhausted, setBrowseExhausted] = useState(false);
  const [browseError, setBrowseError] = useState<string | null>(null);

  // Latest-fired timer ref so a fast-typing operator
  // doesn't race two requests out of order. We only honour
  // the last debounced fire.
  const debounceRef = useRef<number | null>(null);

  // Sentinel ref for infinite scroll: an IntersectionObserver
  // watches this div; when it scrolls into view we fetch
  // the next page.
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // ────────────────────────────────────────────────────────
  // Search mode
  // ────────────────────────────────────────────────────────

  const runSearch = useCallback(async (q: string) => {
    const trimmed = q.trim();
    if (!trimmed) {
      setSearchData(null);
      setSearchError(null);
      setSearchLoading(false);
      return;
    }
    setSearchLoading(true);
    setSearchError(null);
    try {
      const r = await fetch(
        `/api/chat/search?q=${encodeURIComponent(trimmed)}&limit=${SEARCH_LIMIT}`,
        { credentials: "include" },
      );
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as ApiError;
        if (r.status === 503) {
          setSearchError(
            body.detail ??
              "Search is not available in this build (FTS5 missing)",
          );
        } else {
          setSearchError(body.detail ?? `Failed (${r.status})`);
        }
        setSearchData(null);
        return;
      }
      const body = (await r.json()) as SearchResponse;
      setSearchData(body);
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : "Network error");
      setSearchData(null);
    } finally {
      setSearchLoading(false);
    }
  }, []);

  // ────────────────────────────────────────────────────────
  // Browse mode (paginated)
  // ────────────────────────────────────────────────────────

  const loadBrowsePage = useCallback(
    async (offset: number, replace: boolean) => {
      setBrowseLoading(true);
      setBrowseError(null);
      try {
        const r = await fetch(
          `/api/chat/sessions?limit=${BROWSE_PAGE}&offset=${offset}`,
          { credentials: "include" },
        );
        if (!r.ok) {
          const body = (await r.json().catch(() => ({}))) as ApiError;
          setBrowseError(body.detail ?? `Failed (${r.status})`);
          return;
        }
        const body = (await r.json()) as SessionListResponse;
        // Append or replace based on the caller's intent.
        // ``replace=true`` is the initial fetch (offset 0)
        // and any re-fetch after a query-clear.
        setBrowseItems((prev) =>
          replace ? body.items : [...prev, ...body.items],
        );
        setBrowseTotal(body.total);
        setBrowseOffset(offset + body.items.length);
        // Stop paging once we've shown everything.
        if (offset + body.items.length >= body.total) {
          setBrowseExhausted(true);
        } else {
          setBrowseExhausted(false);
        }
      } catch (e) {
        setBrowseError(e instanceof Error ? e.message : "Network error");
      } finally {
        setBrowseLoading(false);
      }
    },
    [],
  );

  // ────────────────────────────────────────────────────────
  // Effects: search vs browse, debounce, infinite scroll
  // ────────────────────────────────────────────────────────

  // Debounced search. Empty input clears search results
  // immediately (no debounce) and triggers a browse-mode
  // reset so the recent-history list comes back.
  useEffect(() => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    if (!query.trim()) {
      setSearchData(null);
      setSearchError(null);
      setSearchLoading(false);
      // Re-prime browse mode: start fresh from offset 0.
      setBrowseItems([]);
      setBrowseTotal(0);
      setBrowseOffset(0);
      setBrowseExhausted(false);
      void loadBrowsePage(0, true);
      return;
    }
    debounceRef.current = window.setTimeout(() => {
      void runSearch(query);
    }, DEBOUNCE_MS);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, [query, runSearch, loadBrowsePage]);

  // First-paint browse fetch. If the user lands on the
  // search pane with no input, this populates the recent
  // list before any effect re-runs.
  useEffect(() => {
    void loadBrowsePage(0, true);
    // Intentionally only on mount — loadBrowsePage's deps
    // are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          // Only fetch when in browse mode (no query), not
          // already loading, and not exhausted.
          if (query.trim()) return;
          if (browseLoading) return;
          if (browseExhausted) return;
          if (browseItems.length === 0 && browseOffset === 0) return;
          void loadBrowsePage(browseOffset, false);
        }
      },
      // 200px root margin so the next page loads slightly
      // before the operator reaches the absolute bottom —
      // no awkward pause at the end of the scroll.
      { rootMargin: "200px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [query, browseLoading, browseExhausted, browseItems.length, browseOffset, loadBrowsePage]);

  // ────────────────────────────────────────────────────────
  // Render
  // ────────────────────────────────────────────────────────

  const inSearchMode = query.trim().length > 0;

  return (
    // ``h-full`` lets us inherit SidebarShell's column
    // height (the shell's outer card is ``h-[calc(100vh-7rem)]``).
    // Previously this was a hard-coded ``h-[560px]`` which
    // left the bottom of the panel empty on viewports
    // taller than ~700 px, and overflowed the sidebar on
    // shorter viewports. Matching the chat / task panes'
    // pattern keeps the layout consistent across tabs.
    <div className="flex flex-col h-full min-h-0">
      <div className="px-6 py-3 border-b border-sky-light/40">
        <h2 className="text-base font-semibold text-ink">{t("chatSearch.title")}</h2>
        <p className="mt-1 text-xs text-ink-soft">
          {inSearchMode ? t("chatSearch.emptyHintSearch") : t("chatSearch.emptyHintBrowse")}
        </p>
      </div>

      <div className="px-6 py-3 border-b border-sky-light/40">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("chatSearch.searchPlaceholder")}
          autoFocus
          aria-label={t("chatSearch.searchAria")}
          className="w-full px-3 py-2 rounded-md border border-sky-light/60 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-ocean/40"
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
              <div className="mt-12 px-4 py-3 rounded-md bg-amber-50 border border-amber-200">
                <p className="text-sm text-amber-800">{searchError}</p>
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
                    key={`${h.session_id}:${h.message_id}`}
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
              <div className="mb-4 px-4 py-3 rounded-md bg-amber-50 border border-amber-200">
                <p className="text-sm text-amber-800">{browseError}</p>
              </div>
            )}

            {browseItems.length === 0 && !browseLoading && !browseError && (
              <p className="text-sm text-ink-soft text-center mt-6">
                {t("chatSearch.emptyBrowse")}
              </p>
            )}

            {browseItems.length > 0 && (
              <ul className="flex flex-col gap-3">
                {browseItems.map((s) => (
                  <SessionSummaryRow
                    key={s.session_id}
                    summary={s}
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
                {browseLoading ? t("chatSearch.loadMore") : ""}
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
  onOpen: (sessionId: string) => void;
}) {
  const t = useT();
  return (
    <li
      className="rounded-lg border border-sky-light/40 bg-white/60 hover:bg-white transition cursor-pointer"
      onClick={() => onOpen(hit.session_id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(hit.session_id);
        }
      }}
    >
      <div className="p-3">
        <div className="flex items-center justify-between gap-2 mb-1">
          <h3 className="text-sm font-medium text-ink truncate">
            {hit.title ?? hit.session_id.slice(0, 13) + "…"}
          </h3>
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-ink-soft border border-sky-light/40 rounded px-1.5 py-0.5">
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
          <span className="truncate font-mono">{hit.session_id}</span>
          <span>{formatTime(hit.ts)}</span>
        </div>
      </div>
    </li>
  );
}

function SessionSummaryRow({
  summary,
  onOpen,
}: {
  summary: SessionSummary;
  onOpen: (sessionId: string) => void;
}) {
  const t = useT();
  // Build a single display line. ``title`` wins (manual or
  // auto-titled); otherwise show the first user message
  // preview (truncated by the backend). The footer shows
  // when the session was last updated.
  const displayTitle = summary.title ?? summary.preview ?? "(空对话)";
  return (
    <li
      className="rounded-lg border border-sky-light/40 bg-white/60 hover:bg-white transition cursor-pointer"
      onClick={() => onOpen(summary.session_id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(summary.session_id);
        }
      }}
    >
      <div className="p-3">
        <div className="flex items-center justify-between gap-2 mb-1">
          <h3 className="text-sm font-medium text-ink truncate">
            {displayTitle}
          </h3>
          <span className="shrink-0 text-[10px] text-ink-soft border border-sky-light/40 rounded px-1.5 py-0.5">
            {t("chatSearch.messageCount").replace("{count}", String(summary.message_count))}
          </span>
        </div>
        <p className="text-xs text-ink-soft line-clamp-2">
          {summary.preview || "—"}
        </p>
        <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-ink-soft">
          <span className="truncate font-mono">
            {summary.session_id.slice(0, 13) + "…"}
          </span>
          <span>{formatRelative(summary.updated_at)}</span>
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