import type { ActionItem, Bus, ExecutableTool } from "@magi/bus";

function idArg(value: unknown, name = "action_item_id"): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) throw new Error(`${name} must be a positive integer`);
  return value;
}

/** Short-lived working reminders have their own Book, separate from durable facts. */
export function actionItemTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "save_action_item",
      description: "Create or update a persistent working note/reminder. Keep current progress and what remains in content. This does not schedule or execute work. Supply an existing ID and its current expected_version to update instead of duplicating it; archived=false restores an inactive note. On version conflict read the current note and reconcile; never blindly retry.",
      input_schema: { type: "object", properties: {
        action_item_id: { type: "integer" }, expected_version: { type: "integer" }, content: { type: "string" }, archived: { type: "boolean" },
      } },
      async run(args) {
        const id = args.action_item_id === undefined ? undefined : idArg(args.action_item_id);
        const expectedVersion = id === undefined ? undefined : idArg(args.expected_version, "expected_version");
        if (id === undefined && args.expected_version !== undefined) throw new Error("expected_version is only valid when updating an action item");
        if (args.content !== undefined && typeof args.content !== "string") throw new Error("content must be a string");
        if (args.archived !== undefined && typeof args.archived !== "boolean") throw new Error("archived must be a boolean");
        return JSON.stringify({ action_item: bus.actionItems.save({ id, expectedVersion, content: args.content as string | undefined, archived: args.archived as boolean | undefined }) });
      },
    },
    {
      name: "list_action_items", description: "Read this MAGI's current working notes; optionally include archived notes. Foreground and background turns share this Book.",
      input_schema: { type: "object", properties: { include_archived: { type: "boolean" } } },
      async run(args) {
        if (args.include_archived !== undefined && typeof args.include_archived !== "boolean") throw new Error("include_archived must be a boolean");
        return JSON.stringify({ action_items: bus.actionItems.list(args.include_archived === true) });
      },
    },
    {
      name: "archive_action_item", description: "Retire a completed, cancelled, or no-longer-relevant working note from active context without deleting its history.",
      input_schema: { type: "object", properties: { action_item_id: { type: "integer" }, expected_version: { type: "integer" } }, required: ["action_item_id", "expected_version"] },
      async run(args) {
        return JSON.stringify({ action_item: bus.actionItems.save({ id: idArg(args.action_item_id), expectedVersion: idArg(args.expected_version, "expected_version"), archived: true }) });
      },
    },
    {
      name: "delete_action_item", description: "Permanently remove a mistaken working note. Prefer archiving notes that are simply no longer active.",
      input_schema: { type: "object", properties: { action_item_id: { type: "integer" }, expected_version: { type: "integer" } }, required: ["action_item_id", "expected_version"] },
      async run(args) {
        const id = idArg(args.action_item_id);
        if (!bus.actionItems.delete(id, idArg(args.expected_version, "expected_version"))) throw new Error(`action item ${id} not found`);
        return JSON.stringify({ deleted: id });
      },
    },
  ];
}

export function actionItemContext(items: ActionItem[]): string {
  if (!items.length) return "";
  return [
    "These are persistent working notes, not scheduled tasks or instructions to execute everything now. Update notes as work progresses; archive them when completed, cancelled, or no longer relevant. Current Book state takes precedence over stale chat summaries. Tool results reflect changes immediately; this block refreshes next turn.",
    ...items.map((item) => `- [${item.id} | version ${item.version}] ${JSON.stringify(item.content)}`),
  ].join("\n");
}
