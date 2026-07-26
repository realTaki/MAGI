/**
 * RunsHistoryDrawer — chat-style log of a task's runs.
 *
 * Mirrors the conversation page bubble layout (user right,
 * assistant left). Fetches the task session via
 * ``GET /api/chat/sessions/{id}`` and overlays per-fire run
 * status from ``GET /api/tasks/{id}/runs``.
 */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../lib/queryClient";

import { formatRunTimestamp } from "./TaskListPane";
import type { TaskRunRow } from "./TaskListPane";

export function RunsHistoryDrawer(props: {
  taskId: string;
  taskName: string;
  sessionId: string | null;
  onClose: () => void;
}) {
  // Chat-style log view for a task's session. Mirrors the
  // main conversation page's bubble layout (see
  // ``ChatTab.tsx`` — user bubbles right-aligned, assistant
  // bubbles left-aligned) so the operator's mental model
  // of "this is just a chat, the timer started it" holds
  // across both surfaces.
  //
  // Source: ``GET /api/chat/sessions/{id}`` returns the full
  // message list for the task's session. The runner's
  // task-creation flow allocates that session (channel =
  // "task"); each cron fire appends one user-message (the
  // contextual prompt) + the agent's reply, possibly with
  // intermediate tool calls.
  //
  // We also fetch ``GET /api/tasks/{id}/runs`` and overlay
  // each fire's status / latency next to the user-message
  // that started it — that's how the operator spots a
  // "成功" reply that didn't actually push to TG (the
  // status pill shows success, the chat shows the agent's
  // reply text, but a quick look at the channel cell on
  // the table tells the operator whether the runner wired
  // ``_tg_send_callback`` for that fire).
  const sessionEnabled = props.sessionId !== null;
  const sessionQuery = useQuery({
    queryKey: props.sessionId ? ["chatSessions", props.sessionId] : ["chatSessions", "none"],
    queryFn: () =>
      apiFetch<{
        session_id: string;
        title: string | null;
        messages: {
          message_id: string;
          role: string;
          ts: string;
          text: string;
        }[];
      }>(`/api/chat/sessions/${props.sessionId}`),
    enabled: sessionEnabled,
  });
  const runsQuery = useQuery({
    queryKey: ["taskRuns", props.taskId],
    queryFn: () => apiFetch<TaskRunRow[]>(`/api/tasks/${props.taskId}/runs`),
  });
  const messages = sessionEnabled ? (sessionQuery.data?.messages ?? null) : [];
  const runs = runsQuery.data ?? null;
  const sessionTitle = sessionEnabled ? (sessionQuery.data?.title ?? null) : null;
  const loadError =
    sessionQuery.error instanceof Error
      ? `加载 session 失败 (${(sessionQuery.error as { status?: number }).status ?? ""})`
      : runsQuery.error instanceof Error
        ? `加载 runs 失败 (${(runsQuery.error as { status?: number }).status ?? ""})`
        : null;

  // Build a quick lookup: user-message ts → matching run
  // (the runner stamps the ChatMessage ts at the same
  // instant as ``TaskRun.started_at`` — see runner.py).
  // Lets the bubble row show "✓ 成功 · 235ms" inline.
  const runByUserTs = new Map<string, TaskRunRow>();
  if (runs && messages) {
    for (const r of runs) {
      // started_at is the runner's wall-clock at the fire;
      // the ChatMessage the runner appended carries the
      // same value as ``ts``. Match on equality.
      runByUserTs.set(r.started_at, r);
    }
  }

  return (
    <div className="fixed inset-0 z-40 bg-black/20 backdrop-blur-sm flex items-center justify-center p-4 overflow-hidden">
      {/* ``h-[calc(100vh-2rem)]`` (fixed) instead of
          ``max-h-…`` so the parent has an explicit
          height context for ``flex-1`` to expand into.
          With ``max-h-``, ``flex-1`` would shrink-to-fit
          the content and ``overflow-y-auto`` on the
          body would never trigger — the drawer would
          just grow past the viewport. ``min-h-0`` on
          the body ensures the child can shrink
          regardless of bubble content height. */}
      <div className="bg-white rounded-xl shadow-2xl max-w-3xl w-full flex flex-col h-[calc(100vh-2rem)]">
        <div className="px-6 py-4 border-b border-sky-light/40 flex items-center justify-between shrink-0">
          <div className="flex flex-col min-w-0">
            <h3 className="text-base font-semibold text-ink truncate">
              {props.taskName}
            </h3>
            <p className="text-xs text-ink-soft mt-0.5">
              {sessionTitle ?? "[定时] session"}
              {props.sessionId
                ? ` · ${props.sessionId.slice(0, 8)}…`
                : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={props.onClose}
            title="关闭"
            aria-label="关闭"
            className="w-7 h-7 inline-flex items-center justify-center rounded-md text-ink-soft hover:text-ink hover:bg-sky-pale/40 transition"
          >
            {/* Left-pointing arrow (←) reads as
                "go back to the table" — matches the
                cancel/back affordance the operator
                expects from a side drawer. ✕ reads
                as "dismiss / delete" which is the
                wrong action. */}
            ←
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-6 space-y-3">
          {loadError && <p className="form-error">✗ {loadError}</p>}
          {props.sessionId === null ? (
            <p className="text-sm text-ink-soft">
              这条任务还没有被 fire 过（session 在第一次 cron
              时由 runner 自动回填）。请先等一次 cron 触发，
              或者用「▶」立刻跑一下让 runner 初始化 session。
            </p>
          ) : messages === null && !loadError ? (
            <p className="text-sm text-ink-soft">加载中…</p>
          ) : messages && messages.length === 0 ? (
            <p className="text-sm text-ink-soft">
              Session 已创建但还没有对话记录。
            </p>
          ) : messages ? (
            messages.map((m) => (
              <div
                key={m.message_id}
                className={
                  "flex " +
                  (m.role === "user" ? "justify-end" : "justify-start")
                }
              >
                <div className="max-w-[80%] min-w-0 space-y-1">
                  <div
                    title={m.role === "user" ? m.text : undefined}
                    className={
                      "rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words " +
                      (m.role === "user"
                        ? "bg-sky-deep text-white"
                        : "bg-sky-pale/60 text-ink border border-sky-light/40")
                    }
                  >
                    {/* The runner prepends a [task context]
                        header (name, schedule, channel,
                        delivery_directive, …) to user
                        messages so the agent has full
                        context when it runs. The operator
                        already sees all of that in the
                        task table though, so we strip the
                        scaffolding here and render only
                        the actual prompt — otherwise each
                        fire's user-bubble takes ~15 lines
                        and the chat scrollback becomes
                        unreadable. Hover the bubble to
                        see the full context if you need
                        it. */}
                    {m.role === "user"
                      ? (m.text.split("[task prompt]\n").pop() ?? m.text)
                      : m.text}
                  </div>
                  {/* Per-fire meta under the user bubble —
                      tells the operator which cron/manual
                      trigger this is and whether it
                      succeeded. */}
                  {m.role === "user" && runByUserTs.has(m.ts) && (
                    <div className="text-[10px] text-ink-soft/80 text-right pr-1">
                      {(() => {
                        const r = runByUserTs.get(m.ts)!;
                        const statusLabel =
                          r.status === "success"
                            ? "✓ 成功"
                            : r.status === "failed"
                              ? "✗ 失败"
                              : r.status === "running"
                                ? "⟳ 执行中"
                                : r.status;
                        const statusColor =
                          r.status === "success"
                            ? "text-emerald-700"
                            : r.status === "failed"
                              ? "text-rose-700"
                              : "text-sky-700";
                        return (
                          <>
                            <span className={statusColor + " font-medium"}>
                              {statusLabel}
                            </span>
                            <span className="ml-1">
                              · {r.trigger === "manual" ? "手动" : "定时"}
                            </span>
                            {r.latency_ms != null && (
                              <span className="ml-1">
                                · {r.latency_ms} ms
                              </span>
                            )}
                            <span
                              className="ml-1"
                              title={r.started_at}
                            >
                              · {formatRunTimestamp(r.started_at)}
                            </span>
                          </>
                        );
                      })()}
                    </div>
                  )}
                </div>
              </div>
            ))
          ) : null}
        </div>
      </div>
    </div>
  );
}

