/**
 * Society handles. One process may speak with two of these.
 *
 * The desktop operator process owns `@shell.magi` (Balthasar) on its BUS and
 * speaks as `@user.magi` from the webapp. The ASP process owns `@asp.magi`
 * (Casper). Standalone MAGIs number from `eva-000`: Melchior is `@eva-000.magi`.
 */

/** Operator speech from the webapp (the human). */
export const USER_HANDLE = "@user.magi";

/** Host MAGI of the desktop operator process. Display name Balthasar. */
export const SHELL_MAGI_HANDLE = "@shell.magi";
export const SHELL_MAGI_NICKNAME = "BALTHASAR";

/** Host MAGI of the ASP process. Display name Casper. */
export const ASP_MAGI_HANDLE = "@asp.magi";
export const ASP_MAGI_NICKNAME = "CASPER";

/** Local name from a handle (`@eva-000.magi` → `eva-000`). */
export function magiLocalName(handle: string): string {
  return String(handle ?? "").replace(/^@/, "").replace(/\.magi$/, "");
}

/** `@<name>.magi` from a node name or handle. */
export function magiHandle(name: string): string {
  return `@${magiLocalName(name)}.magi`;
}

/** Sequential standalone MAGI names: `eva-000`, `eva-001`, … */
export function magiNodeName(index: number): string {
  return `eva-${String(index).padStart(3, "0")}`;
}

export function sequentialMagiHandle(index: number): string {
  return magiHandle(magiNodeName(index));
}

/** Next unused `eva-NNN` among already-taken handles. */
export function nextMagiName(takenHandles: Iterable<string>): string {
  const taken = takenHandles instanceof Set ? takenHandles : new Set(takenHandles);
  let index = 0;
  for (;;) {
    if (!taken.has(sequentialMagiHandle(index))) return magiNodeName(index);
    index += 1;
  }
}

/** The one seeded classic EVA child. Display name Melchior. */
export const EVA_MAGI_HANDLE = sequentialMagiHandle(0);
export const EVA_MAGI_NICKNAME = "MELCHIOR";

const IN_PROCESS_MAGI_HANDLES = new Set([SHELL_MAGI_HANDLE, ASP_MAGI_HANDLE]);

/** True when this MAGI lives in Shell or ASP, not in a spawned `eva.js`. */
export function isInProcessMagiHandle(handle: string): boolean {
  return IN_PROCESS_MAGI_HANDLES.has(handle);
}

/** Built-in role names are defaults, independent of a user-selected nickname. */
export function defaultDisplayName(handle: string): string {
  if (handle === SHELL_MAGI_HANDLE) return SHELL_MAGI_NICKNAME;
  if (handle === ASP_MAGI_HANDLE) return ASP_MAGI_NICKNAME;
  if (handle === EVA_MAGI_HANDLE) return EVA_MAGI_NICKNAME;
  return handle;
}
