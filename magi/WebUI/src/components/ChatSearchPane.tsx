/**
 * ChatSearchPane — D.18 full-text search across chat history.
 *
 * Replaces the static "搜索对话" placeholder that the
 * sidebar's action panel rendered before D.18. The pane:
 *
 *   1. Debounces user input (300ms) to avoid spamming the
 *      backend while the operator is typing.
 *   2. Calls ``GET /api/chat/search?q=...&limit=20`` with the
 *      standard ``credentials: "include"`` so the admin
 *      cookie gates the request and the server scopes the
 *      results to the calling chat_id.
 *   3. Renders one row per match: a snippet with
 *      ``<mark>``-highlighted hits, the session title (or
 *      the session id fallback), and the role tag.
 *   4. Click on a row → ``onOpen(session_id)`` — the
 *      parent's ``openSession`` helper loads the full
 *      session + flips to the conversation pane (the
 *      deep-link into the matching thread).
 *
 * Backend contract (chat_search.py):
 *   - 200 → {q, chat_id, items: [{session_id, message_id,
 *     role, ts, snippet, title, score}], total, limit, offset}
 *   - 401 → no admin cookie
 *   - 503 → "search.unavailable" (FTS5 missing in this build)
 *   - 400 → "search.bad_query" (FTS5 syntax error, post-
 *     sanitization shouldn't happen but defended)
 *
 * The frontend never tries to ``unmark`` the snippets; the
 * backend ships them with ``<mark>...</mark>`` already in
 * place (see chat_search.py ``snippet()`` call). The default
 * browser style for ``<mark>`` is yellow, which is loud in
 * our pastel palette — see ``styles.css`` for the tuned
 * ``mark { background: ... }`` override.
 */

import { useCallback, useEffect, useRef, useState } from "react";

type SearchHit = {
  session_id: string;
  message_id: string;
  role: "user" | "assistant" | "system";
  ts: string;
  snippet: string;
  title: string | null;
  score: number;
};

type SearchResponse = {
  q: string;
  chat_id: string;
  items: SearchHit[];
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

export default function ChatSearchPane({ onOpen }: Props) {
  const [query, setQuery] = useState("");
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Latest-fired timer ref so a fast-typing operator doesn't
  // race two requests out of order. We only honour the last
  // debounced fire.
  const debounceRef = useRef<number | null>(null);

  const run = useCallback(async (q: string) => {
    const trimmed = q.trim();
    if (!trimmed) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(
        `/api/chat/search?q=${encodeURIComponent(trimmed)}&limit=${SEARCH_LIMIT}`,
        { credentials: "include" },
      );
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as ApiError;
        // 503 is a build-level "FTS5 missing" — show a
        // distinctive hint so the operator (or an SRE
        // reading the UI) understands it's not a query
        // problem.
        if (r.status === 503) {
          setError(
            body.detail ??
              "Search is not available in this build (FTS5 missing)",
          );
        } else {
          setError(body.detail ?? `Failed (${r.status})`);
        }
        setData(null);
        return;
      }
      const body = (await r.json()) as SearchResponse;
      setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // Debounce user input. A trailing-edge debounce is fine
  // here — we don't want to show stale results while
  // typing.
  useEffect(() => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    if (!query.trim()) {
      // Empty input clears immediately, no debounce.
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    debounceRef.current = window.setTimeout(() => {
      void run(query);
    }, DEBOUNCE_MS);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, [query, run]);

  return (
    <div className="flex flex-col h-[560px]">
      <div className="px-6 py-3 border-b border-sky-light/40">
        <h2 className="text-base font-semibold text-ink">搜索对话</h2>
        <p className="mt-1 text-xs text-ink-soft">
          跨所有 session 的全文搜索。中英文至少 3 个字符起才能匹配。
        </p>
      </div>

      <div className="px-6 py-3 border-b border-sky-light/40">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="输入关键字..."
          autoFocus
          aria-label="搜索关键字"
          className="w-full px-3 py-2 rounded-md border border-sky-light/60 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-ocean/40"
        />
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading && (
          <p className="text-sm text-ink-soft text-center mt-12">
            搜索中…
          </p>
        )}

        {!loading && error && (
          <div className="mt-12 px-4 py-3 rounded-md bg-amber-50 border border-amber-200">
            <p className="text-sm text-amber-800">{error}</p>
          </div>
        )}

        {!loading && !error && data && data.items.length === 0 && (
          <p className="text-sm text-ink-soft text-center mt-12">
            没有匹配的对话。
          </p>
        )}

        {!loading && !error && !query.trim() && (
          <p className="text-sm text-ink-soft text-center mt-12">
            输入关键字开始搜索。
          </p>
        )}

        {!loading && !error && data && data.items.length > 0 && (
          <ul className="space-y-3">
            {data.items.map((h) => (
              <li
                key={`${h.session_id}:${h.message_id}`}
                className="rounded-lg border border-sky-light/40 bg-white/60 hover:bg-white transition cursor-pointer"
                onClick={() => onOpen(h.session_id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpen(h.session_id);
                  }
                }}
              >
                <div className="p-3">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <h3 className="text-sm font-medium text-ink truncate">
                      {h.title ?? h.session_id.slice(0, 13) + "…"}
                    </h3>
                    <span className="shrink-0 text-[10px] uppercase tracking-wide text-ink-soft border border-sky-light/40 rounded px-1.5 py-0.5">
                      {h.role === "user"
                        ? "你"
                        : h.role === "assistant"
                          ? "EVE"
                          : "sys"}
                    </span>
                  </div>
                  <p
                    className="text-xs text-ink-soft leading-relaxed"
                    // The backend ships the snippet with
                    // <mark>...</mark> already in place;
                    // rendered as HTML so the highlight
                    // shows up.
                    dangerouslySetInnerHTML={{ __html: h.snippet }}
                  />
                  <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-ink-soft">
                    <span className="truncate font-mono">
                      {h.session_id}
                    </span>
                    <span>{formatTime(h.ts)}</span>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {data && data.total > data.items.length && (
        <div className="px-6 py-2 border-t border-sky-light/40">
          <p className="text-xs text-ink-soft text-center">
            显示前 {data.items.length} 条，共 {data.total} 条匹配
          </p>
        </div>
      )}
    </div>
  );
}

function formatTime(iso: string): string {
  // Localised-ish: "2026-07-05 14:32". ``Date`` is good enough
  // for v0 — the backend's ISO UTC string is what we're
  // formatting.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}