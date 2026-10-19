/**
 * Rebuild this checkout and replace the process. The replace worker owns the
 * build, the health check, and the rollback. This tool returns as soon as the
 * job is queued, so a long build is not reported as a failure.
 */

import type { Bus, ExecutableTool } from "@magi/bus";
import { editableSource } from "./source.js";

export function replaceTools(bus: Bus): ExecutableTool[] {
  return [{
    name: "replace_self",
    description: "Rebuild this node's editable source (Shell's startup checkout, or this node's MAGI/) and replace this process. No branch commit or push is needed. Returns the job id and status immediately. A failed build is rolled back and reported in the chat.",
    input_schema: { type: "object", properties: {} },
    async run() {
      const id = bus.board("ReplaceSelfJob").publish({ checkout: editableSource(bus), swap: true }, "tools");
      return JSON.stringify({ id, status: "pending" });
    },
  }];
}
