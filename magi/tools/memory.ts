/** Long-term memory: create or update, archive, delete. */

import type { Bus, ExecutableTool, MemoryKind } from "../bus/index.js";
import { integerArg } from "./args.js";

function memoryKind(value: unknown): MemoryKind {
  if (value === "temporary" || value === "short_term" || value === "long_term") return value;
  throw new Error("kind must be temporary, short_term, or long_term");
}

export function memoryTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "save_memory", description: "Create or update a memory for future conversations.",
      input_schema: { type: "object", properties: {
        memory_id: { type: "integer" }, topic: { type: "string" }, detail: { type: "string" },
        kind: { type: "string", enum: ["temporary", "short_term", "long_term"] }, archived: { type: "boolean" },
      } },
      async run(args) {
        const id = typeof args.memory_id === "number" && Number.isInteger(args.memory_id) ? args.memory_id : undefined;
        const kind = args.kind === undefined ? undefined : memoryKind(args.kind);
        const memory = bus.memoryBook.save({
          id, topic: typeof args.topic === "string" ? args.topic : undefined,
          detail: typeof args.detail === "string" ? args.detail : undefined,
          kind, archived: typeof args.archived === "boolean" ? args.archived : undefined,
        });
        return JSON.stringify({ memory });
      },
    },
    {
      name: "complete_memory", description: "Archive a memory after it is no longer active.",
      input_schema: { type: "object", properties: { memory_id: { type: "integer" } }, required: ["memory_id"] },
      async run(args) {
        const id = integerArg(args, "memory_id");
        return JSON.stringify({ memory: bus.memoryBook.save({ id, archived: true }) });
      },
    },
    {
      name: "delete_memory", description: "Permanently delete one memory.",
      input_schema: { type: "object", properties: { memory_id: { type: "integer" } }, required: ["memory_id"] },
      async run(args) {
        const id = integerArg(args, "memory_id");
        if (!bus.memoryBook.delete(id)) throw new Error(`memory ${id} not found`);
        return JSON.stringify({ deleted: id });
      },
    },
  ];
}
