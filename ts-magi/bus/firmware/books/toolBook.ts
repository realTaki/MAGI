import type { LLMTool } from "../jobs/types.js";

export type ExecutableTool = LLMTool & { run(args: Record<string, unknown>): Promise<string> };

/** The BUS-owned catalog shared by Agent, Tools, and MCP workers. */
export class ToolBook {
  private readonly sources = new Map<string, Map<string, ExecutableTool>>();

  replaceSource(source: string, tools: ExecutableTool[]): void {
    const next = new Map<string, ExecutableTool>();
    for (const tool of tools) {
      if (next.has(tool.name)) throw new Error(`duplicate tool ${tool.name} in ${source}`);
      for (const [owner, registered] of this.sources) {
        if (owner !== source && registered.has(tool.name)) throw new Error(`tool ${tool.name} is already registered by ${owner}`);
      }
      next.set(tool.name, tool);
    }
    this.sources.set(source, next);
  }

  get(name: string): ExecutableTool | null {
    for (const tools of this.sources.values()) {
      const tool = tools.get(name);
      if (tool) return tool;
    }
    return null;
  }

  catalog(): LLMTool[] {
    return [...this.sources.values()].flatMap((source) => [...source.values()].map(({ name, description, input_schema }) => ({ name, description, input_schema })));
  }
}
