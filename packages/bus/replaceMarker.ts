import { join } from "node:path";
import { magiHome } from "@magi/runtime/home.js";
import { magiLocalName } from "./identity.js";

/** Where a failed revision switch is recorded. The child reads the same path. */
export function replaceFailedPath(handle: string): string {
  return join(magiHome(), magiLocalName(handle), "replace-failed.json");
}
