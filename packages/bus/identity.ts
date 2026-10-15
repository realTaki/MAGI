/**
 * Society handles. One process may speak with two of these.
 *
 * The desktop operator process owns `@eva-000.magi` (Balthasar) on its BUS and
 * speaks as `@user.magi` from the webapp. The ASP process owns `@eva-001.magi`
 * (Casper). Only `@eva-002.magi` (Melchior) is a classic spawned EVA child.
 */

/** Operator speech from the webapp (the human). */
export const USER_HANDLE = "@user.magi";

/** Host MAGI of the desktop operator process. Display name Balthasar. */
export const SHELL_MAGI_HANDLE = "@eva-000.magi";
export const SHELL_MAGI_NICKNAME = "BALTHASAR";

/** Host MAGI of the ASP process. Display name Casper. */
export const ASP_MAGI_HANDLE = "@eva-001.magi";
export const ASP_MAGI_NICKNAME = "CASPER";

/** The one seeded classic EVA child. Display name Melchior. */
export const EVA_MAGI_HANDLE = "@eva-002.magi";
export const EVA_MAGI_NICKNAME = "MELCHIOR";

const IN_PROCESS_MAGI_HANDLES = new Set([SHELL_MAGI_HANDLE, ASP_MAGI_HANDLE]);

/** True when this MAGI lives in Shell or ASP, not in a spawned `eva.js`. */
export function isInProcessMagiHandle(handle: string): boolean {
  return IN_PROCESS_MAGI_HANDLES.has(handle);
}
