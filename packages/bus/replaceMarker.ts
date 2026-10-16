import { homedir } from "node:os";
import { join } from "node:path";

/** Where the shell records a replace whose old process did not start. The app reads the same path. */
export function replaceFailedPath(handle: string): string {
  const localName = handle.replace(/^@/, "").replace(/\.magi$/, "");
  return join(homedir(), ".magi", localName, "replace-failed.json");
}
