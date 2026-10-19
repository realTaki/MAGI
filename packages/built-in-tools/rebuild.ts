/**
 * Ask this node's launcher to replace the running process. Compile and tests
 * belong in the agent's own turn; this tool only queues the parent-owned swap.
 */

import type { Bus, ExecutableTool } from "@magi/bus";
import { SHELL_MAGI_HANDLE } from "@magi/bus";
import { stringArg } from "./args.js";
import { editableSource } from "./source.js";

export function rebuildTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "request_replace",
      description: "Request that this node's launcher replace the running process with a new revision of the editable source. Call only after you already compiled successfully and tests are all green. This process cannot safely stop and restart itself; the parent drains, switches version, health-checks, and rolls back on failure. For an explicit complete Linux desktop upgrade on packaged Balthasar, set desktop=true to build and activate its AppImage too. Returns the job id.",
      input_schema: {
        type: "object",
        properties: { target: { type: "string", enum: ["self"] }, desktop: { type: "boolean", description: "Rebuild and replace the complete Linux AppImage as well as this Shell revision. Only for packaged Balthasar." } },
        required: ["target"],
      },
      async run(args) {
        const target = stringArg(args, "target");
        if (target !== "self") throw new Error("each node replaces itself; start that node's eva launcher");
        const desktop = args.desktop === true;
        if (desktop && (process.platform !== "linux" || process.env.MAGI_PACKAGED !== "1" || process.env.MAGI_ROLE !== "shell" || bus.handle !== SHELL_MAGI_HANDLE)) throw new Error("Desktop self-update requires the packaged Linux Balthasar Shell");
        const chat_id = typeof args.chat_id === "number" && Number.isInteger(args.chat_id) ? args.chat_id : undefined;
        const id = bus.board("ReplaceSelfJob").publish({
          checkout: editableSource(bus),
          swap: true,
          ...(desktop ? { desktop: true } : {}),
          ...(chat_id !== undefined ? { chat_id } : {}),
        }, "tools");
        if (chat_id !== undefined) bus.settings.set("replace.request_chat_id", String(chat_id));
        return JSON.stringify({ id, status: "pending" });
      },
    },
  ];
}
