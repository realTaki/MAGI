/**
 * The workspace-file tools, owned by the worker that runs them.
 *
 * Like `@magi/skills` and `@magi/mcp`, a tool lives with the worker that owns
 * it: the catalog offers these exactly while this worker runs, so a stopped
 * worker cannot leave the model holding a tool nobody answers.
 */

import { BaseWorker, type Bus, type ExecutableTool } from "@magi/bus";
import { fileTools } from "./tools.js";

export class FilesWorker extends BaseWorker {
  readonly worker_name = "files";
  private readonly own: ExecutableTool[];
  private readonly pending = new Set<Promise<void>>();
  private started = false;

  constructor(bus: Bus) {
    super(bus);
    this.own = fileTools(bus.workspace);
    bus.tools.registerSource("files", () => (this.started ? this.own : []));
  }

  async start(): Promise<void> { this.started = true; }

  async poll(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name, (input) => this.own.some((tool) => tool.name === input.call.name));
    if (!job) return false;
    const task = this.run(job.id, job.input.call.name, job.input.call.arguments);
    this.pending.add(task);
    void task.finally(() => this.pending.delete(task));
    return true;
  }

  private async run(id: number, name: string, args: Record<string, unknown>): Promise<void> {
    const board = this.bus.board("RunToolJob");
    try {
      const tool = this.own.find((candidate) => candidate.name === name);
      if (!tool) throw new Error(`unknown tool ${name}`);
      board.submit(this.worker_name, id, { output: { content: await tool.run(args) } });
    } catch (error) {
      board.submit(this.worker_name, id, { error: error instanceof Error ? error.message : String(error) });
    }
  }

  /** Stopping lets the calls this worker already accepted finish. */
  async stop(): Promise<void> { this.started = false; await Promise.all(this.pending); }
}
