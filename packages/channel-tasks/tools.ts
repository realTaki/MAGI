/**
 * The tool that makes a task: the model picks a friendly frequency, and this
 * turns it into the cron the worker beside it scans.
 *
 * It lives here because the task does: this package is what stores a task,
 * watches for it, and fires it, so the tool belongs to the same owner as the
 * loop that honours it. See `worker.ts`.
 */

import type { Bus, ExecutableTool } from "@magi/bus";

/** Local on purpose: this package must not depend on the tools package. */
function stringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  if (typeof value !== "string" || !value) throw new Error(`${key} must be a non-empty string`);
  return value;
}

function positiveInteger(value: unknown, key: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) throw new Error(`${key} must be a positive integer`);
  return value;
}

function boundedInteger(value: unknown, key: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) throw new Error(`${key} must be an integer from ${min} to ${max}`);
  return value;
}

function presetCron(args: Record<string, unknown>): string {
  const frequency = args.frequency;
  const minute = boundedInteger(args.minute ?? 0, "minute", 0, 59);
  const hour = boundedInteger(args.hour ?? 0, "hour", 0, 23);
  if (frequency === "hourly") return `${minute} * * * *`;
  if (frequency === "daily") return `${minute} ${hour} * * *`;
  if (frequency === "weekly") {
    const day = boundedInteger(args.day_of_week, "day_of_week", 0, 6);
    return `${minute} ${hour} * * ${day === 6 ? 0 : day + 1}`;
  }
  if (frequency === "monthly") return `${minute} ${hour} ${boundedInteger(args.day_of_month, "day_of_month", 1, 31)} * *`;
  throw new Error("frequency must be hourly, daily, weekly, or monthly");
}

export function taskTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "schedule_task", description: "Create or update a recurring task for a chat.",
      input_schema: { type: "object", properties: {
        name: { type: "string" }, prompt: { type: "string" },
        frequency: { type: "string", enum: ["hourly", "daily", "weekly", "monthly"] },
        hour: { type: "integer", minimum: 0, maximum: 23 }, minute: { type: "integer", minimum: 0, maximum: 59 },
        day_of_week: { type: "integer", minimum: 0, maximum: 6 }, day_of_month: { type: "integer", minimum: 1, maximum: 31 },
        chat_id: { type: "integer" },
      }, required: ["name", "prompt", "frequency", "chat_id"] },
      async run(args) {
        const name = stringArg(args, "name");
        const prompt = stringArg(args, "prompt");
        const chatId = positiveInteger(args.chat_id, "chat_id");
        if (!bus.chats.get(chatId)) throw new Error(`unknown chat ${chatId}`);
        const task = bus.tasks.save({ name, prompt, cron: presetCron(args), chat_id: chatId });
        return JSON.stringify({ task_id: task.id, name: task.name, cron: task.cron, chat_id: task.chat_id });
      },
    },
  ];
}
