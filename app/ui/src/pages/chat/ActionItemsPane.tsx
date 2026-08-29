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

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { InfoTip } from "../../components/InfoTip";
import { useT } from "../../i18n/index";
import { apiFetch, qk } from "../../lib/queryClient";

type ActionItem = {
  id: number;
  title: string;
  description: string | null;
  target_url: string | null;
  priority: "normal" | "high";
  due_date: string | null;
  source: "system" | "eva" | "user";
  created_at: string;
  completed_at: string | null;
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

/** Format an ISO UTC datetime into a compact date (MM-DD or
 *  YYYY-MM-DD if the year differs from now). Returns "" on
 *  null or unparseable input. */
function formatDateOnly(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  if (d.getFullYear() !== now.getFullYear()) {
    return `${d.getFullYear()}-${mm}-${dd}`;
  }
  return `${mm}-${dd}`;
}

/** Returns true if the given ISO date is before today (UTC). */
function isOverdue(iso: string | null): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);
  return d < today;
}

export default function ActionItemsPane() {
  const t = useT();
  const qc = useQueryClient();
  const [inflight, setInflight] = useState<Set<number>>(new Set());
  const [completionError, setCompletionError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: qk.actionItems,
    queryFn: () => apiFetch<ActionItemListResponse>("/api/action_items"),
  });
  const data = query.data ?? null;
  const error = query.error ? (query.error instanceof Error ? query.error.message : "failed") : null;

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
    const previous = query.data;
    // Optimistic update
    if (previous) {
      qc.setQueryData(qk.actionItems, {
        ...previous,
        items: previous.items.map((row: ActionItem) =>
          row.id === it.id
            ? { ...row, completed_at: new Date().toISOString() }
            : row,
        ),
      });
    }
    setCompletionError(null);
    try {
      const r = await fetch(`/api/action_items/${it.id}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({}),
      });
      if (!r.ok) {
        qc.setQueryData(qk.actionItems, previous); // rollback
        const body = (await r.json().catch(() => ({}))) as ApiError;
        setCompletionError(body.detail ?? `Failed (${r.status})`);
      } else {
        void qc.invalidateQueries({ queryKey: qk.actionItems });
      }
    } catch (e) {
      qc.setQueryData(qk.actionItems, previous); // rollback
      setCompletionError(e instanceof Error ? e.message : "Network error");
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
        <div className="px-6 py-3 border-b border-border flex items-center gap-2">
          <h2 className="text-base font-semibold text-ink">{t("actionItems.title")}</h2>
          <InfoTip text={t("actionItems.description")} />
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
        <div className="px-6 py-3 border-b border-border flex items-center gap-2">
          <h2 className="text-base font-semibold text-ink">{t("actionItems.title")}</h2>
          <InfoTip text={t("actionItems.description")} />
        </div>
        <div className="flex-1 flex items-center justify-center">
          <p className="text-sm text-ink-soft">Loading…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[560px]">
      <div className="px-6 py-3 border-b border-border flex items-center gap-2">
        <h2 className="text-base font-semibold text-ink">{t("actionItems.title")}</h2>
        <InfoTip text={t("actionItems.description")} />
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {open.length === 0 ? (
          <p className="text-sm text-ink-soft text-center mt-12">
            {t("actionItems.empty")}
          </p>
        ) : (
          open.map((it) => (
            <div
              key={it.id}
              className="rounded-lg border border-border bg-surface p-4 flex items-start gap-3"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-medium text-ink">{it.title}</h3>
                  {it.priority === "high" && (
                    <span className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-danger-soft text-danger">
                      {t("actionItems.priorityHigh")}
                    </span>
                  )}
                </div>
                {it.description && (
                  <p className="mt-1 text-xs text-ink-soft">
                    {it.description}
                  </p>
                )}
                {it.due_date && (
                  <p className={`mt-1 text-xs ${isOverdue(it.due_date) ? "text-danger font-medium" : "text-ink-soft"}`}>
                    {t("actionItems.dueDate")}: {formatDateOnly(it.due_date)}
                    {isOverdue(it.due_date) ? ` · ${t("actionItems.overdue")}` : ""}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {it.target_url && (
                  <a
                    href={it.target_url}
                    className="btn btn-secondary text-xs"
                  >
                    {t("actionItems.goToSettings")}
                  </a>
                )}
                <button
                  type="button"
                  onClick={() => complete(it)}
                  disabled={inflight.has(it.id)}
                  className="btn btn-primary text-xs disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {t("actionItems.complete")}
                </button>
              </div>
            </div>
          ))
        )}

        {completed.length > 0 && (
          <details className="mt-4">
            <summary className="text-xs text-ink-soft cursor-pointer select-none">
              {t("actionItems.recentCompleted")} ({completed.length})
            </summary>
            <ul className="mt-2 space-y-1">
              {completed.map((c) => (
                <li
                  key={c.id}
                  className="text-xs text-ink-soft flex items-center justify-between gap-3 px-1"
                >
                  <span className="truncate">{c.title}</span>
                  <span className="shrink-0 text-accent-ink">
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
      {completionError && (
        <div className="mx-6 mb-2">
          <p className="form-error">✗ {completionError}</p>
        </div>
      )}
    </div>
  );
}
