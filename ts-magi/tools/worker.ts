import { BaseWorker } from "../bus/index.js";
import type { Bus } from "../bus/index.js";
import { builtinTools, type Tool } from "./registry.js";

export class ToolsWorker extends BaseWorker {
  readonly worker_name = "tools";
  private readonly tools: Map<string, Tool>;

  constructor(bus: Bus, tools = builtinTools(bus.workspace)) {
    super(bus);
    this.tools = new Map(tools.map((tool) => [tool.name, tool]));
  }

  catalog() { return [...this.tools.values()].map(({ name, description, input_schema }) => ({ name, description, input_schema })); }

  async poll(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    const { name, arguments: args } = job.input.call;
    try {
      const tool = this.tools.get(name);
      if (!tool) throw new Error(`unknown tool ${name}`);
      board.submit(this.worker_name, job.id, { output: { content: await tool.run(args) } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }
}
