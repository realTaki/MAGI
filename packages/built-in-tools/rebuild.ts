/**
 * Queue a replace of this node. The child builds, then asks its launcher to
 * switch revision. This process does not rebuild another node.
 */

import type { Bus, ExecutableTool } from "@magi/bus";
import { stringArg } from "./args.js";
import { editableSource } from "./source.js";

export function rebuildTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "request_rebuild",
      description: "Queue a replace of this node. The child snapshots its editable source (Shell's startup checkout, or this node's MAGI/), tests and builds it, then asks its launcher to switch revision. No commit on your branch or push is needed. Returns the job id. Another node replaces itself the same way.",
      input_schema: {
        type: "object",
        properties: { target: { type: "string", enum: ["self"] } },
        required: ["target"],
      },
      async run(args) {
        const target = stringArg(args, "target");
        if (target !== "self") throw new Error("each node replaces itself; start that node's eva launcher");
        const id = bus.board("ReplaceSelfJob").publish({ checkout: editableSource(bus), swap: true }, "tools");
        return JSON.stringify({ id, status: "pending" });
      },
    },
  ];
}
