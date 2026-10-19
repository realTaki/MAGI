/**
 * Rebuild this MAGI's own checkout and, when asked, replace the running
 * process. The replace worker drains, stops the old workers, health-checks
 * the new build, and only then marks the job done. A failed build or health
 * check restores the previous output.
 */

export type ReplaceSelfJob = {
  /** The checkout to build. Omitted, the running app's own checkout is used. */
  checkout?: string;
  /** Commit or ref to check out. Omitted, snapshot the checkout’s saved local source. An explicit ref fetches a committed remote upgrade. */
  ref?: string;
  /** After a healthy build, hand this process to its supervisor or a new one. */
  swap?: boolean;
  /** Chat that requested the replace; a safety-gate failure wakes this chat. */
  chat_id?: number;
};

export type ReplaceSelfResult = { replaced: true };
