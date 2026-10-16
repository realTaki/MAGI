/**
 * Rebuild this MAGI's own checkout and, when asked, replace the running
 * process. The replace worker drains, stops the old workers, health-checks
 * the new build, and only then marks the job done. A failed build or health
 * check restores the previous output.
 */

export type ReplaceSelfJob = {
  /** The checkout to build. Omitted, the running app's own checkout is used. */
  checkout?: string;
  /** After a healthy build, hand this process to its supervisor or a new one. */
  swap?: boolean;
};

export type ReplaceSelfResult = { replaced: true };
