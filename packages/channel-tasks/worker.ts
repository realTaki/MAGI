/**
 * Tasks: a chat that speaks up on its own.
 *
 * This package owns the whole path — the tool that describes a task
 * (`tools.ts`), the scan that decides one is due, and the message that fires
 * it. Both halves travel with this one worker: it registers `schedule_task`
 * while it runs and claims the calls for it, and a due task is fired right
 * here instead of being announced to the BUS for someone to pick up.
 */

import { BaseWorker, SYSTEM_CONTACT_ID, type Bus, type ExecutableTool, type Task } from "@magi/bus";
import { taskTools } from "./tools.js";

/** Scanning the book costs more than claiming a job; a second is plenty for minute precision. */
const SCAN_MS = 1_000;

export class TaskWorker extends BaseWorker {
  readonly worker_name = "task";
  private readonly own: ExecutableTool[];
  private started = false;
  private lastScan = 0;

  constructor(bus: Bus, private readonly options: { isReady?: () => boolean } = {}) {
    super(bus);
    this.own = taskTools(bus);
    // Offered only while this worker runs: a stopped worker would leave the model
    // holding a tool whose tasks nobody scans or fires.
    bus.tools.registerSource("tasks", () => (this.started ? this.own : []));
  }

  async start(): Promise<void> { this.started = true; }

  async poll(): Promise<boolean> {
    if (!this.started) return false;
    if (await this.pollToolCall()) return true;
    if (Date.now() - this.lastScan < SCAN_MS) return false;
    this.lastScan = Date.now();
    return this.fireDue();
  }

  /** `schedule_task` runs here, like any tool a worker owns the name of. */
  private async pollToolCall(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name, (input) => this.own.some((tool) => tool.name === input.call.name));
    if (!job) return false;
    const tool = this.own.find((candidate) => candidate.name === job.input.call.name);
    try {
      if (!tool) throw new Error(`unknown tool ${job.input.call.name}`);
      board.submit(this.worker_name, job.id, { output: { content: await tool.run(this.scopedArguments(job.input.chat_id, job.input.call.name, job.input.call.arguments)) } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  private scopedArguments(chatId: number | undefined, name: string, input: Record<string, unknown>): Record<string, unknown> {
    if (chatId === undefined || this.bus.chats.get(chatId)?.channel !== "subconscious") return input;
    const args = { ...input };
    const existingRunAt = name === "update_task" && args.enabled === true && args.frequency === undefined
      ? this.bus.tasks.get(Number(args.task_id))?.run_at : null;
    if (existingRunAt && Date.parse(existingRunAt) < Date.now() + 60 * 60_000) {
      throw new Error("reschedule a completed subconscious one-time task at least one hour ahead before enabling it");
    }
    if (args.frequency === "once" && (typeof args.run_at !== "string" || Date.parse(args.run_at) < Date.now() + 60 * 60_000)) {
      throw new Error("subconscious one-time wakes must be at least one hour in the future; use action items for immediate pending work");
    }
    if (name === "list_tasks") return { chat_id: chatId };
    if (name === "schedule_task") {
      if (args.target !== "subconscious" && args.chat_id !== chatId) throw new Error("subconscious tasks must target the private self conversation");
      args.chat_id = chatId;
    } else if (this.bus.tasks.get(Number(args.task_id))?.chat_id !== chatId) {
      throw new Error("cannot change another conversation's task");
    }
    return args;
  }

  private fireDue(): boolean {
    const now = new Date();
    const minute = now.toISOString().slice(0, 16);
    let fired = false;
    for (const task of this.bus.tasks.list(true)) {
      if (this.bus.chats.get(task.chat_id)?.channel === "subconscious" && this.options.isReady?.() === false) continue;
      if (task.last_fired_minute === minute || (task.run_at ? Date.parse(task.run_at) > now.getTime() : !cronMatches(task.cron, now))) continue;
      const stored = this.bus.tasks.fire(task.id, minute, () => this.persist(task));
      if (!stored) continue;
      this.bus.deliveries.enqueue(this.bus, stored.delivery);
      fired = true;
    }
    return fired;
  }

  private persist(task: Task) {
    return this.bus.deliveries.persist({
      chat_id: task.chat_id, contact_id: SYSTEM_CONTACT_ID, publisher: this.worker_name,
      content: `[task context]\nYou are EXECUTING a scheduled task that just fired.\nname: ${task.name}\nschedule: ${task.run_at ?? task.cron}\n\n[task prompt]\n${task.prompt}`,
    });
  }

  async stop(): Promise<void> { this.started = false; }
}

export function cronMatches(expression: string, date: Date): boolean {
  const fields = expression.trim().split(/\s+/);
  if (fields.length !== 5) return false;
  const values = [date.getUTCMinutes(), date.getUTCHours(), date.getUTCDate(), date.getUTCMonth() + 1, date.getUTCDay()];
  const minimums = [0, 0, 1, 1, 0];
  const maximums = [59, 23, 31, 12, 7];
  return fields.every((field, index) => {
    if (index !== 4) return cronFieldMatches(field, values[index], minimums[index], maximums[index]);
    return cronFieldMatches(field, values[index], 0, 7) || (values[index] === 0 && cronFieldMatches(field, 7, 0, 7));
  });
}

function cronFieldMatches(field: string, value: number, min: number, max: number): boolean {
  return field.split(",").some((part) => {
    const [range, stepRaw] = part.split("/");
    const step = stepRaw === undefined ? 1 : Number(stepRaw);
    if (!Number.isInteger(step) || step < 1) return false;
    let start = min; let end = max;
    if (range !== "*") {
      const bounds = range.split("-").map(Number);
      start = bounds[0]; end = bounds.length === 1 ? bounds[0] : bounds[1];
    }
    return Number.isInteger(start) && Number.isInteger(end) && start >= min && end <= max && value >= start && value <= end && (value - start) % step === 0;
  });
}
