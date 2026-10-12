/**
 * The only magi:// link the desktop accepts. Shell owns processing; this compatibility parser is also used
 * by older callers. Anything else is refused.
 *
 * magi://runtime/rebuild?target=self&handle=%40eva-000.magi
 */

const HANDLE = /^@[A-Za-z0-9][A-Za-z0-9._-]{0,62}\.magi$/;
const TARGETS = new Set(["self", "app", "asp"]);

export type RebuildLink = { target: "self" | "app" | "asp"; handle: string };

export function parseRebuildLink(raw: string): RebuildLink | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "magi:" || url.port !== "") return null;
  if (url.username !== "" || url.password !== "") return null;
  if (url.hostname !== "runtime" || url.pathname.replace(/\/+$/, "") !== "/rebuild") return null;
  if (url.hash !== "") return null;
  const keys = [...url.searchParams.keys()];
  if (keys.length !== 2 || !keys.includes("target") || !keys.includes("handle")) return null;
  const target = url.searchParams.get("target");
  const handle = url.searchParams.get("handle") ?? "";
  if (target === null || !TARGETS.has(target) || !HANDLE.test(handle)) return null;
  return { target: target as RebuildLink["target"], handle };
}
