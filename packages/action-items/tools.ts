import type { ActionItem, ActionItemCategory, Bus, ExecutableTool } from "@magi/bus";

function idArg(value: unknown, name = "action_item_id"): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) throw new Error(`${name} must be a positive integer`);
  return value;
}

function categoryArg(value: unknown): ActionItemCategory | undefined {
  if (value !== undefined && value !== "todo" && value !== "idea" && value !== "goal") throw new Error("category must be todo, idea, or goal");
  return value;
}

/** Short-lived working reminders have their own Book, separate from durable facts. */
export function actionItemTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "save_action_item",
      description: "Create or update a persistent working note: todo for work to track, idea for an unprioritized possibility, not a commitment; goal for a long-term desired outcome. For goals keep background, success criteria, current evidence and remaining gaps in content; completing a related todo does not complete the goal. New notes default to todo; omitted category preserves an existing note's category. Foreground and subconscious may organize ideas as conversation develops; archive withdrawn ideas rather than delete them. Keep current progress and what remains in content. This does not schedule or execute work. Supply an existing ID and its current expected_version to update instead of duplicating it; archived=false restores an inactive note. On version conflict read the current note and reconcile; never blindly retry.",
      input_schema: { type: "object", properties: {
        action_item_id: { type: "integer" }, expected_version: { type: "integer" }, content: { type: "string" }, category: { type: "string", enum: ["todo", "idea", "goal"] }, archived: { type: "boolean" },
      } },
      async run(args) {
        const id = args.action_item_id === undefined ? undefined : idArg(args.action_item_id);
        const expectedVersion = id === undefined ? undefined : idArg(args.expected_version, "expected_version");
        if (id === undefined && args.expected_version !== undefined) throw new Error("expected_version is only valid when updating an action item");
        if (args.content !== undefined && typeof args.content !== "string") throw new Error("content must be a string");
        if (args.archived !== undefined && typeof args.archived !== "boolean") throw new Error("archived must be a boolean");
        return JSON.stringify({ action_item: bus.actionItems.save({ id, expectedVersion, content: args.content as string | undefined, category: categoryArg(args.category), archived: args.archived as boolean | undefined }) });
      },
    },
    {
      name: "list_action_items", description: "Read this MAGI's current working notes; optionally filter category (todo, idea, or goal) and include archived notes. Returned order is stable ID order, not urgency or importance. Foreground and background turns share this Book.",
      input_schema: { type: "object", properties: { include_archived: { type: "boolean" }, category: { type: "string", enum: ["todo", "idea", "goal"] } } },
      async run(args) {
        if (args.include_archived !== undefined && typeof args.include_archived !== "boolean") throw new Error("include_archived must be a boolean");
        return JSON.stringify({ action_items: bus.actionItems.list(args.include_archived === true, categoryArg(args.category)) });
      },
    },
    {
      name: "archive_action_item", description: "Retire a completed, cancelled, or no-longer-relevant todo, or withdraw an idea or an achieved/abandoned/no-longer-relevant goal, from active context without deleting its history.",
      input_schema: { type: "object", properties: { action_item_id: { type: "integer" }, expected_version: { type: "integer" } }, required: ["action_item_id", "expected_version"] },
      async run(args) {
        return JSON.stringify({ action_item: bus.actionItems.save({ id: idArg(args.action_item_id), expectedVersion: idArg(args.expected_version, "expected_version"), archived: true }) });
      },
    },
    {
      name: "delete_action_item", description: "Permanently remove a mistaken working note. Archive completed or inactive todos, withdrawn ideas, and achieved/abandoned goals instead; a withdrawn idea is not a mistaken note.",
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
  const active = items.filter(item => !item.archived);
  if (!active.length) return "";
  const todos = active.filter(item => item.category === "todo");
  const goals = active.filter(item => item.category === "goal");
  const ideas = active.filter(item => item.category === "idea");
  const lines = (group: ActionItem[]) => group.map(item => `- [${item.id} | version ${item.version}] ${JSON.stringify(item.content)}`);
  return [
    "These are persistent working notes, not scheduled tasks or instructions to execute everything now. Update notes as work progresses; archive completed/cancelled todos and withdrawn or no-longer-relevant ideas without deleting their history. Archive goals only when their outcome is achieved, explicitly abandoned, or no longer relevant, not merely because a related todo finished. Current Book state takes precedence over stale chat summaries. Tool results reflect changes immediately; this block refreshes next turn.",
    ...(todos.length ? ["Todos: assess urgency and importance from their content and current conversation. This stable ID order is not a priority ranking.", ...lines(todos)] : []),
    ...(goals.length ? ["Goals: long-term outcomes combining working progress and durable background. Keep background, success criteria, current evidence and remaining gaps in content. Review progress against the outcome, not just completed todos; never automatically archive a goal when a related step finishes. These notes are not scheduling or permission to act.", ...lines(goals)] : []),
    ...(ideas.length ? ["Ideas: unprioritized possibilities, not commitments, deadlines, or instructions to execute. Foreground and subconscious may refine or archive these using the same action-item tools as conversation develops. Do not turn an idea into a todo without evidence of intended work. Mention an idea naturally only when relevant; do not present it as an outstanding obligation.", ...lines(ideas)] : []),
  ].join("\n");
}
