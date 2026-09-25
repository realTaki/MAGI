import { BaseWorker, type Task } from "../../bus/index.js";

export class TaskWorker extends BaseWorker {
  readonly worker_name = "task";
  private lastScan = 0;

  async poll(): Promise<boolean> {
    const board = this.bus.board("RunTaskNotify");
    const trigger = board.claim(this.worker_name);
    if (trigger) {
      const task = this.bus.tasks.get(trigger.input.task_id);
      if (!task) board.submit(this.worker_name, trigger.id, { error: `task ${trigger.input.task_id} does not exist` });
      else { this.fire(task, trigger.input.manual ?? false); board.submit(this.worker_name, trigger.id, { output: {} }); }
      return true;
    }
    if (Date.now() - this.lastScan < 1_000) return false;
    this.lastScan = Date.now();
    const now = new Date();
    const minute = now.toISOString().slice(0, 16);
    for (const task of this.bus.tasks.list(true)) {
      if (task.last_fired_minute !== minute && cronMatches(task.cron, now)) {
        task.last_fired_minute = minute;
        this.bus.tasks.markFired(task.id, minute);
        board.publish({ task_id: task.id, manual: false }, this.worker_name);
      }
    }
    return false;
  }

  private fire(task: Task, manual: boolean): void {
    this.bus.publishChat({
      chat_id: task.chat_id,
      text: `[task context]\nYou are EXECUTING a scheduled task that just fired.\nname: ${task.name}\nschedule: ${manual ? "manual" : task.cron}\n\n[task prompt]\n${task.prompt}`,
    }, this.worker_name);
  }
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
