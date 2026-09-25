/**
 * Skills are progressive disclosure: the system prompt lists their summaries,
 * and the model pulls a full SKILL.md through this tool only when needed.
 *
 * The files themselves belong to this package — see `worker.ts`.
 */

import type { Bus, ExecutableTool } from "@magi/bus";

/** Local on purpose: this package must not depend on the tools package. */
function stringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  if (typeof value !== "string" || !value) throw new Error(`${key} must be a non-empty string`);
  return value;
}

export function skillTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "load_skill", description: "Load the full SKILL.md instructions for one available skill.",
      input_schema: { type: "object", properties: { name: { type: "string" } }, required: ["name"] },
      async run(args) {
        const name = stringArg(args, "name");
        const content = bus.skills.read(name);
        if (content === null) throw new Error(`skill ${name} not found`);
        return content.slice(0, 32 * 1024);
      },
    },
  ];
}
