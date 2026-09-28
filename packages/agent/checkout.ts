/**
 * The one fact the prompt is allowed to state about this process's source.
 * The repository map stays in a skill. This only says whether that map applies.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";

export function sourceCheckout(handle: string, workspace: string): string {
  const name = handle.replace(/^@/, "").replace(/\.magi$/, "");
  const present = existsSync(join(workspace, "MAGI", ".git"));
  return present
    ? `handle: ${handle}\ncheckout: MAGI/\nbranch: magi/${name}\nThis is the only tree you may edit.`
    : `handle: ${handle}\ncheckout: none\nThis process has no source worktree. Do not edit product source and do not request a rebuild.`;
}
