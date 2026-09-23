import { BaseWorker } from "../bus/index.js";
import type { Bus } from "../bus/index.js";
import { builtinTools, type Tool } from "./registry.js";
import { ShellManager } from "./shellManager.js";

export class ToolsWorker extends BaseWorker {
  readonly worker_name = "tools";
  private readonly tools: Map<string, Tool>;
  private readonly shells = new ShellManager();
  private readonly sources = new Map<string, string[]>();
  private readonly pending = new Set<Promise<void>>();

  constructor(bus: Bus, tools?: Tool[]) {
    super(bus);
    this.tools = new Map((tools ?? builtinTools(bus, this.shells)).map((tool) => [tool.name, tool]));
  }

  catalog() { return [...this.tools.values()].map(({ name, description, input_schema }) => ({ name, description, input_schema })); }

  replaceSource(source: string, tools: Tool[]): void {
    for (const name of this.sources.get(source) ?? []) this.tools.delete(name);
    for (const tool of tools) this.tools.set(tool.name, tool);
    this.sources.set(source, tools.map((tool) => tool.name));
  }

  async poll(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    const task = this.run(job.id, job.input.call.name, job.input.call.arguments);
    this.pending.add(task);
    void task.finally(() => this.pending.delete(task));
    return true;
  }

  private async run(id: number, name: string, args: Record<string, unknown>): Promise<void> {
    const board = this.bus.board("RunToolJob");
    try {
      const tool = this.tools.get(name);
      if (!tool) throw new Error(`unknown tool ${name}`);
      board.submit(this.worker_name, id, { output: { content: await tool.run(args) } });
    } catch (error) {
      board.submit(this.worker_name, id, { error: error instanceof Error ? error.message : String(error) });
    }
  }

  async stop(): Promise<void> { await Promise.all(this.pending); await this.shells.shutdown(); }
}
