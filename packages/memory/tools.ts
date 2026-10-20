/** Long-term memory: create or update, archive, delete. */

import type { Bus, ExecutableTool, MemoryKind } from "@magi/bus";

/** Local on purpose: this package must not depend on the tools package. */
function integerArg(args: Record<string, unknown>, key: string): number {
  const value = args[key];
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) throw new Error(`${key} must be a positive integer`);
  return value;
}

function memoryKind(value: unknown): MemoryKind {
  if (value === "temporary" || value === "short_term" || value === "long_term") return value;
  throw new Error("kind must be temporary, short_term, or long_term");
}

export function memoryTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "list_memory", description: "Read current memories and versions before creating, updating, or archiving. Include archived memories to avoid reviving obsolete facts or creating duplicates.",
      input_schema: { type: "object", properties: { include_archived: { type: "boolean" } } },
      async run(args) {
        if (args.include_archived !== undefined && typeof args.include_archived !== "boolean") throw new Error("include_archived must be a boolean");
        return JSON.stringify({ memories: bus.memoryBook.list(args.include_archived === true) });
      },
    },
    {
      name: "save_memory", description: "Create or update a memory for future chats. Creating requires topic and detail. Updating requires memory_id and the current expected_version from list_memory; on conflict reread and reconcile, never blindly retry.",
      input_schema: { type: "object", properties: {
        memory_id: { type: "integer", minimum: 1 }, expected_version: { type: "integer", minimum: 1 },
        topic: { type: "string" }, detail: { type: "string" },
        kind: { type: "string", enum: ["temporary", "short_term", "long_term"] }, archived: { type: "boolean" },
      } },
      async run(args) {
        const id = args.memory_id === undefined ? undefined : integerArg(args, "memory_id");
        const expectedVersion = args.expected_version === undefined ? undefined : integerArg(args, "expected_version");
        for (const key of ["topic", "detail"]) if (args[key] !== undefined && typeof args[key] !== "string") throw new Error(`${key} must be a string`);
        if (args.archived !== undefined && typeof args.archived !== "boolean") throw new Error("archived must be a boolean");
        const kind = args.kind === undefined ? undefined : memoryKind(args.kind);
        const memory = bus.memoryBook.save({
          id, expectedVersion, topic: typeof args.topic === "string" ? args.topic : undefined,
          detail: typeof args.detail === "string" ? args.detail : undefined,
          kind, archived: typeof args.archived === "boolean" ? args.archived : undefined,
        });
        return JSON.stringify({ memory });
      },
    },
    {
      name: "complete_memory", description: "Archive a memory after it is no longer active. Requires its current version from list_memory; on conflict reread and reconcile.",
      input_schema: { type: "object", properties: { memory_id: { type: "integer", minimum: 1 }, expected_version: { type: "integer", minimum: 1 } }, required: ["memory_id", "expected_version"] },
      async run(args) {
        const id = integerArg(args, "memory_id");
        return JSON.stringify({ memory: bus.memoryBook.save({ id, archived: true, expectedVersion: integerArg(args, "expected_version") }) });
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
