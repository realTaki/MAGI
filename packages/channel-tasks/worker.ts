/**
 * Tasks: a chat that speaks up on its own.
 *
 * This package owns the whole path — the tool that describes a task
 * (`tools.ts`), the scan that decides one is due, and the message that fires
 * it. Both halves travel with this one worker: it registers `schedule_task`
 * while it runs and claims the calls for it, and a due task is fired right
 * here instead of being announced to the BUS for someone to pick up.
 */

import { BaseWorker, messageDelivery, type Bus, type ExecutableTool, type Task } from "@magi/bus";
import { taskTools } from "./tools.js";

/** Scanning the book costs more than claiming a job; a second is plenty for minute precision. */
const SCAN_MS = 1_000;

export class TaskWorker extends BaseWorker {
  readonly worker_name = "task";
  private readonly own: ExecutableTool[];
  private started = false;
  private lastScan = 0;

  constructor(bus: Bus) {
    super(bus);
    this.own = taskTools(bus);
    // Offered only while this worker runs: a stopped worker would leave the model
    // holding a tool whose tasks nobody scans or fires.
    bus.tools.registerSource("tasks", () => (this.started ? this.own : []));
  }

  async start(): Promise<void> { this.started = true; }

  async poll(): Promise<boolean> {
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
      board.submit(this.worker_name, job.id, { output: { content: await tool.run(job.input.call.arguments) } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  private fireDue(): boolean {
    const now = new Date();
    const minute = now.toISOString().slice(0, 16);
    let fired = false;
    for (const task of this.bus.tasks.list(true)) {
      if (task.last_fired_minute === minute || !cronMatches(task.cron, now)) continue;
      this.bus.tasks.markFired(task.id, minute);
      this.fire(task);
      fired = true;
    }
    return fired;
  }

  private fire(task: Task): void {
    messageDelivery.send(this.bus, {
      chat_id: task.chat_id,
      text: `[task context]\nYou are EXECUTING a scheduled task that just fired.\nname: ${task.name}\nschedule: ${task.cron}\n\n[task prompt]\n${task.prompt}`,
    }, this.worker_name);
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
