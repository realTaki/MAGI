import { join } from "node:path";
import { magiHome } from "@magi/runtime/home.js";

/** Where a failed revision switch is recorded. The child reads the same path. */
export function replaceFailedPath(handle: string): string {
  const localName = handle.replace(/^@/, "").replace(/\.magi$/, "");
  return join(magiHome(), localName, "replace-failed.json");
}
