/**
 * ActionItemsPane — the right-side panel for the "Action Items"
 * sidebar entry in the chat nav.
 *
 * Each row is one to-do the operator should act on (today: a
 * reminder to set their LLM provider + API key when they first
 * land on the dashboard after onboarding). The pane fetches
 * `GET /api/action_items` on mount + each time the operator
 * navigates away and back; the "完成" button POSTs
 * `/api/action_items/{id}/complete` with optimistic UI
 * (the row disappears the moment the request fires, and
 * comes back if the request fails).
 *
 * UI states:
 *   - `loading`: first-load fetch in flight
 *   - `error`: request failed; show the backend `detail`
 *   - empty: no open items, no recent completions
 *   - populated: open list with optional "最近完成" disclosure
 *
 * `server_time` from the API is used as the clock anchor for
 * "3h ago" stamps so the operator's own clock skew doesn't
 * make completions look like future-dated history.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

type ActionItem = {
  id: number;
  kind: string;
  title: string;
  description: string | null;
  target_url: string | null;
  priority: "normal" | "high";
  source: "system" | "eve" | "user";
  created_at: string;
  completed_at: string | null;
  completed_by_employee_id: number | null;
  completion_note: string | null;
  dismissed: boolean;
};

type ActionItemListResponse = {
  items: ActionItem[];
  server_time: string;
};

type ApiError = { code?: string; detail?: string };

function formatRelative(
  iso: string,
  serverIso: string,
): string {
  // Anchor to the server's clock, not the client's, so a
  // wrong-time laptop doesn't show "刚刚完成" for a row the
  // server stamped five minutes ago. Both inputs are UTC ISO
  // strings produced by the backend.
  const past = new Date(iso).getTime();
  const now = new Date(serverIso).getTime();
  if (Number.isNaN(past) || Number.isNaN(now)) return iso;
  const deltaSec = Math.max(0, Math.round((now - past) / 1000));
  if (deltaSec < 60) return "刚刚";
  const mins = Math.round(deltaSec / 60);
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} 天前`;
  return iso.slice(0, 10);
}

export default function ActionItemsPane() {
  const [data, setData] = useState<ActionItemListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Items currently mid-complete — disables the "完成" button
  // so a slow network doesn't trigger two POSTs.
  const [inflight, setInflight] = useState<Set<number>>(new Set());

  const load = useCallback(async () => {
    setError(null);
    try {
      const r = await fetch("/api/action_items", {
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as ApiError;
        setError(body.detail ?? `Failed (${r.status})`);
        return;
      }
      const body = (await r.json()) as ActionItemListResponse;
      setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Open rows: never completed, never dismissed. The backend
  // already returns them in the right order (open first, then
  // by priority/recency). Completed rows show under "最近完成".
  const open = useMemo(
    () =>
      (data?.items ?? []).filter(
        (it) => it.completed_at === null && !it.dismissed,
      ),
    [data],
  );
  const completed = useMemo(
    () =>
      (data?.items ?? []).filter((it) => it.completed_at !== null),
    [data],
  );
  const serverTime = data?.server_time ?? null;

  async function complete(it: ActionItem) {
    if (inflight.has(it.id)) return;
    setInflight((s) => {
      const next = new Set(s);
      next.add(it.id);
      return next;
    });
    // Optimistic remove — keep the response from the server in
    // case it differs (e.g. it was a no-op because someone
    // already completed it on a different tab).
    const previous = data;
    setData((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((row) =>
              row.id === it.id
                ? { ...row, completed_at: new Date().toISOString() }
                : row,
            ),
          }
        : prev,
    );
    try {
      const r = await fetch(`/api/action_items/${it.id}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({}),
      });
      if (!r.ok) {
        // Rollback — the optimistic stamp was premature.
        setData(previous);
        const body = (await r.json().catch(() => ({}))) as ApiError;
        setError(body.detail ?? `Failed (${r.status})`);
      } else {
        // Re-fetch so the row is now in `completed` with the
        // server's official `completed_at` + `server_time`.
        load();
      }
    } catch (e) {
      setData(previous);
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setInflight((s) => {
        const next = new Set(s);
        next.delete(it.id);
        return next;
      });
    }
  }

  // Render branches. Order matters: error short-circuits even
  // if data is null, so a transient failure doesn't get hidden
  // under "Loading…".
  if (error && data === null) {
    return (
      <div className="p-8 flex flex-col h-[560px]">
        <div className="px-6 py-3 border-b border-sky-light/40">
          <h2 className="text-base font-semibold text-ink">Action Items</h2>
          <p className="text-xs text-ink-soft">
            给你的待办。第一次进入 dashboard 时按"OK, got it"完成上线的提醒在这里。
          </p>
        </div>
        <div className="flex-1 flex items-center justify-center px-6">
          <p className="form-error">✗ {error}</p>
        </div>
      </div>
    );
  }

  if (data === null) {
    return (
      <div className="p-8 flex flex-col h-[560px]">
        <div className="px-6 py-3 border-b border-sky-light/40">
          <h2 className="text-base font-semibold text-ink">Action Items</h2>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <p className="text-sm text-ink-soft">Loading…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[560px]">
      <div className="px-6 py-3 border-b border-sky-light/40">
        <h2 className="text-base font-semibold text-ink">Action Items</h2>
        <p className="text-xs text-ink-soft">
          给你的待办。第一次进入 dashboard 时按"OK, got it"完成上线的提醒在这里。
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {open.length === 0 ? (
          <p className="text-sm text-ink-soft text-center mt-12">
            没有待办 — 都搞定了。
          </p>
        ) : (
          open.map((it) => (
            <div
              key={it.id}
              className="rounded-lg border border-sky-light/40 bg-white/60 p-4 flex items-start gap-3"
            >
              <div className="flex-1 min-w-0">
                <h3 className="text-sm font-medium text-ink">{it.title}</h3>
                {it.description && (
                  <p className="mt-1 text-xs text-ink-soft">
                    {it.description}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {it.target_url && (
                  <a
                    href={it.target_url}
                    className="btn btn-secondary text-xs"
                  >
                    去设置
                  </a>
                )}
                <button
                  type="button"
                  onClick={() => complete(it)}
                  disabled={inflight.has(it.id)}
                  className="btn btn-primary text-xs disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  完成
                </button>
              </div>
            </div>
          ))
        )}

        {completed.length > 0 && (
          <details className="mt-4">
            <summary className="text-xs text-ink-soft cursor-pointer select-none">
              最近完成 ({completed.length})
            </summary>
            <ul className="mt-2 space-y-1">
              {completed.map((c) => (
                <li
                  key={c.id}
                  className="text-xs text-ink-soft flex items-center justify-between gap-3 px-1"
                >
                  <span className="truncate">{c.title}</span>
                  <span className="shrink-0 text-ocean">
                    {c.completed_at && serverTime
                      ? formatRelative(c.completed_at, serverTime)
                      : ""}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>

      {error && (
        <div className="mx-6 mb-2">
          <p className="form-error">✗ {error}</p>
        </div>
      )}
    </div>
  );
}
