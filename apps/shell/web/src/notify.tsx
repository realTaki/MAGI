/**
 * The one place the interface keeps its own errors.
 *
 * A failure that belongs to a conversation — an agent turn, a channel, a task —
 * is delivered into chat by MAGI itself, and that is where the operator reads it.
 * What is left is the interface's own trouble: a bridge call that failed, a build
 * that threw, a stray rejection with no chat to attach to. Those land here, and a
 * single component draws them, so an error stops appearing as loose text in
 * whichever corner of the UI happened to be running — and so a richer notification
 * surface can replace this look later without touching a caller.
 */

import { useEffect, useSyncExternalStore } from "react";
import { useT } from "./i18n";

export type Notice = { id: number; text: string };

/** Long enough to read a build failure, short enough not to pile up. */
const DISMISS_AFTER_MS = 12_000;

let notices: Notice[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function drop(id: number): void {
  notices = notices.filter((notice) => notice.id !== id);
  emit();
}

export function dismissNotice(id: number): void {
  drop(id);
}

/** Report an error the interface cannot place anywhere better. */
export function notifyError(error: unknown): void {
  const text = (error instanceof Error ? error.message : String(error ?? "")).trim();
  if (text === "") return;
  // The same failure twice (a retry, a service that keeps refusing) is one notice.
  if (notices.some((notice) => notice.text === text)) return;
  const id = nextId++;
  notices = [...notices, { id, text }];
  emit();
  setTimeout(() => drop(id), DISMISS_AFTER_MS);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function NoticeStack() {
  const t = useT();
  const current = useSyncExternalStore(subscribe, () => notices);

  // Anything that escapes a handler ends up here as well: no failure should stay
  // unseen just because nobody caught it.
  useEffect(() => {
    const onError = (event: ErrorEvent) => notifyError(event.error ?? event.message);
    const onRejection = (event: PromiseRejectionEvent) => notifyError(event.reason);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  if (current.length === 0) {
    return null;
  }
  return (
    <div className="notice-stack" role="alert" aria-live="assertive">
      {current.map((notice) => (
        <div key={notice.id} className="notice">
          <span className="notice__mark" aria-hidden="true">
            !
          </span>
          <span className="notice__body">
            <span className="notice__title">{t("common.error")}</span>
            <span className="notice__text">{notice.text}</span>
          </span>
          <button
            type="button"
            className="notice__close"
            aria-label={t("common.close")}
            onClick={() => dismissNotice(notice.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
