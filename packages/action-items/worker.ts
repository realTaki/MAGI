/** Shared working notes, independent of both foreground and subconscious agents.
 * The BUS owns persistence; this worker owns CRUD execution and live context.
 * Notes are not scheduled tasks: this worker never creates wakes or agent turns.
 */
import { BaseWorker, type Bus, type ExecutableTool } from "@magi/bus";
import { actionItemContext, actionItemTools } from "./tools.js";

export class ActionItemsWorker extends BaseWorker {
  readonly worker_name = "action-items";
  private readonly own: ExecutableTool[];
  private readonly pending = new Set<Promise<void>>();
  private started = false;

  constructor(bus: Bus) {
    super(bus);
    this.own = actionItemTools(bus);
    bus.tools.registerSource("action-items", () => this.started ? this.own : []);
    bus.prompts.registerSource("action-items", "Action items", () =>
      this.started ? actionItemContext(bus.actionItems.list()) : "");
  }

  async start(): Promise<void> { this.started = true; }

  async poll(): Promise<boolean> {
    if (!this.started) return false;
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name, input => this.own.some(tool => tool.name === input.call.name));
    if (!job) return false;
    const task = this.run(job.id, job.input.call.name, job.input.call.arguments);
    this.pending.add(task);
    void task.finally(() => this.pending.delete(task));
    return true;
  }

  private async run(id: number, name: string, args: Record<string, unknown>): Promise<void> {
    const board = this.bus.board("RunToolJob");
    try {
      const tool = this.own.find(candidate => candidate.name === name);
      if (!tool) throw new Error(`unknown tool ${name}`);
      board.submit(this.worker_name, id, { output: { content: await tool.run(args) } });
    } catch (error) {
      board.submit(this.worker_name, id, { error: error instanceof Error ? error.message : String(error) });
    }
  }

  /** Withdraw tools/context immediately, then drain already-accepted calls. */
  async stop(): Promise<void> { this.started = false; await Promise.all(this.pending); }
}
