/**
 * Recurring work. The model picks a friendly frequency; the tool turns it into
 * the cron expression the TaskWorker scans.
 */

import type { Bus, ExecutableTool } from "@magi/bus";
import { boundedInteger, integerArg, stringArg } from "./args.js";

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
        const chatId = integerArg(args, "chat_id");
        if (!bus.chats.get(chatId)) throw new Error(`unknown chat ${chatId}`);
        const cron = presetCron(args);
        const task = bus.tasks.save({ name, prompt, cron, chat_id: chatId });
        return JSON.stringify({ task_id: task.id, name: task.name, cron: task.cron, chat_id: task.chat_id });
      },
    },
  ];
}
