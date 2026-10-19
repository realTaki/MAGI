/**
 * Ask this node's launcher to replace the running process. Compile and tests
 * belong in the agent's own turn; this tool only queues the parent-owned swap.
 */

import type { Bus, ExecutableTool } from "@magi/bus";
import { editableSource } from "./source.js";

export function replaceTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "request_replace",
      description: "Request that this node's launcher replace the running process with a new revision of the editable source. Call only after you already compiled successfully and tests are all green. This process cannot safely stop and restart itself; the parent drains, switches version, health-checks, and rolls back on failure. Returns the job id.",
      input_schema: {
        type: "object",
        properties: {},
      },
      async run(_args, _signal, context) {
        const chat_id = typeof context?.chat_id === "number" && Number.isInteger(context.chat_id) && context.chat_id > 0 ? context.chat_id : undefined;
        const id = bus.board("ReplaceSelfJob").publish({
          checkout: editableSource(bus),
          swap: true,
          ...(chat_id !== undefined ? { chat_id } : {}),
        }, "tools");
        return JSON.stringify({ id, status: "pending" });
      },
    },
  ];
}
