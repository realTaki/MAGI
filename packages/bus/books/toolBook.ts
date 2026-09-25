/** What a model may be offered: a catalog entry, without the way to run it. */
export type LLMTool = { name: string; description: string; input_schema: Record<string, unknown> };

export type ExecutableTool = LLMTool & { run(args: Record<string, unknown>): Promise<string> };

/** A source reports the tools it owns right now; it is asked, never pushed to. */
export type ToolSource = () => ExecutableTool[];

/**
 * The BUS-owned catalog shared by Agent, Tools, and MCP workers: what a model may
 * be offered, one name per tool. Every read asks the live sources, so a worker that
 * stopped — or dropped a connection — is simply not in the answer. Execution stays
 * with the worker that owns a tool: each claims the calls for its own names.
 */
export class ToolBook {
  private readonly sources = new Map<string, ToolSource>();

  /** Re-registering is how a source asks "am I still valid?" — a clash must fail here, not mid-turn. */
  registerSource(source: string, tools: ToolSource): void {
    const previous = this.sources.get(source);
    this.sources.set(source, tools);
    try {
      this.snapshot();
    } catch (error) {
      if (previous) this.sources.set(source, previous); else this.sources.delete(source);
      throw error;
    }
  }

  get(name: string): ExecutableTool | null {
    return this.snapshot().get(name) ?? null;
  }

  catalog(): LLMTool[] {
    return [...this.snapshot().values()].map(({ name, description, input_schema }) => ({ name, description, input_schema }));
  }

  private snapshot(): Map<string, ExecutableTool> {
    const tools = new Map<string, ExecutableTool>();
    const owners = new Map<string, string>();
    for (const [source, provide] of this.sources) {
      for (const tool of provide()) {
        const owner = owners.get(tool.name);
        if (owner === source) throw new Error(`duplicate tool ${tool.name} in ${source}`);
        if (owner) throw new Error(`tool ${tool.name} is already registered by ${owner}`);
        owners.set(tool.name, source);
        tools.set(tool.name, tool);
      }
    }
    return tools;
  }
}
