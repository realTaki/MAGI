import { BaseWorker } from "../bus/index.js";
import type { Bus } from "../bus/index.js";
import { builtinTools, type Tool } from "./registry.js";
import { ShellManager } from "./shellManager.js";

export class ToolsWorker extends BaseWorker {
  readonly worker_name = "tools";
  private readonly shells = new ShellManager();
  private readonly pending = new Set<Promise<void>>();
  /** The tools this worker runs. The BUS catalog is what a model is offered. */
  private readonly own = new Map<string, Tool>();

  constructor(bus: Bus, tools?: Tool[]) {
    super(bus);
    const builtin = tools ?? builtinTools(bus, this.shells);
    for (const tool of builtin) this.own.set(tool.name, tool);
    bus.tools.replaceSource("builtin", builtin);
  }

  async poll(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name, (input) => this.own.has(input.call.name));
    if (!job) return false;
    const task = this.run(job.id, job.input.call.name, job.input.call.arguments);
    this.pending.add(task);
    void task.finally(() => this.pending.delete(task));
    return true;
  }

  private async run(id: number, name: string, args: Record<string, unknown>): Promise<void> {
    const board = this.bus.board("RunToolJob");
    try {
      const tool = this.own.get(name);
      if (!tool) throw new Error(`unknown tool ${name}`);
      board.submit(this.worker_name, id, { output: { content: await tool.run(args) } });
    } catch (error) {
      board.submit(this.worker_name, id, { error: error instanceof Error ? error.message : String(error) });
    }
  }

  async stop(): Promise<void> { await Promise.all(this.pending); await this.shells.shutdown(); }
}
