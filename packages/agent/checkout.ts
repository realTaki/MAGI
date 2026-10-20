/**
 * The one fact the prompt is allowed to state about this process's source.
 * The repository map stays in a skill. This only says whether that map applies.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";
import { magiLocalName } from "@magi/bus";

export function sourceCheckout(handle: string, workspace: string, role = process.env.MAGI_ROLE): string {
  const name = magiLocalName(handle);
  const branch = role === "shell" ? `magi/${role}` : `magi/${name}`;
  const managed = join(workspace, "MAGI");
  const envCheckout = process.env.MAGI_CHECKOUT?.trim() ?? "";
  const root = existsSync(join(managed, ".git"))
    ? managed
    : (role === "shell") && envCheckout !== "" && existsSync(join(envCheckout, ".git"))
      ? envCheckout
      : "";
  if (root === "") {
    return `handle: ${handle}\ncheckout: none\nThis process has no source checkout. Do not edit product source and do not request a replace.`;
  }
  const checkout = root === managed ? "MAGI/" : root;
  return `handle: ${handle}\ncheckout: ${checkout}\nbranch: ${branch}\nThis is the only tree you may edit.`;
}
