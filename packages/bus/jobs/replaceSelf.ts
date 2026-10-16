/**
 * Rebuild this MAGI's own checkout and, when asked, replace the running process.
 * The manager drains, builds, health-checks, and restores the previous build
 * when any of that fails.
 */

export type ReplaceSelfJob = {
  checkout: string;
  /** Spawn the built entry and retire this process after it reports ready. */
  swap?: boolean;
};

export type ReplaceSelfResult = { replaced: true };
