import { Client, SSEClientTransport, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";
import { BaseWorker, type Bus, type ExecutableTool, type McpServerConfig } from "../bus/index.js";

export type McpConnection = { tools: ExecutableTool[]; close(): Promise<void> };
export type McpConnector = (config: McpServerConfig, workspace: string) => Promise<McpConnection>;

export class McpWorker extends BaseWorker {
  readonly worker_name = "mcp";
  private readonly connections = new Map<string, McpConnection>();
  /** The MCP tools this worker runs: their connections live here, so their calls do too. */
  private readonly own = new Map<string, ExecutableTool>();
  private readonly pending = new Set<Promise<void>>();
  private started = false;

  constructor(bus: Bus, private readonly connector: McpConnector = connectMcpServer) { super(bus); }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    await Promise.all(this.bus.mcpServers.list().filter((server) => server.enabled !== false).map(async (server) => {
      try { this.connections.set(server.name, await this.connector(server, this.bus.workspace)); }
      catch (error) { console.error(`MCP ${server.name}:`, error); }
    }));
    this.inject();
  }

  async poll(): Promise<boolean> {
    if (await this.pollChanges()) return true;
    return this.pollToolCall();
  }

  private async pollChanges(): Promise<boolean> {
    const board = this.bus.board("ChangeMcpServerNotify");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    try {
      const before = [...this.own.keys()];
      const old = this.connections.get(job.input.name);
      if (job.input.action === "delete") {
        if (old) await old.close();
        this.connections.delete(job.input.name);
        this.bus.mcpServers.delete(job.input.name);
      } else {
        const server = job.input.server;
        if (!server) throw new Error("MCP server configuration is missing");
        const connected = server.enabled === false ? null : await this.connector(server, this.bus.workspace);
        if (old) await old.close();
        if (connected) this.connections.set(server.name, connected); else this.connections.delete(server.name);
        this.bus.mcpServers.save(server);
      }
      this.inject();
      this.drainDropped(before);
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  private async pollToolCall(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name, (input) => this.own.has(input.call.name));
    if (!job) return false;
    const task = this.callTool(job.id, job.input.call.name, job.input.call.arguments);
    this.pending.add(task);
    void task.finally(() => this.pending.delete(task));
    return true;
  }

  private async callTool(id: number, name: string, args: Record<string, unknown>): Promise<void> {
    const board = this.bus.board("RunToolJob");
    try {
      const tool = this.own.get(name);
      if (!tool) throw new Error(`unknown tool ${name}`);
      board.submit(this.worker_name, id, { output: { content: await tool.run(args) } });
    } catch (error) {
      board.submit(this.worker_name, id, { error: error instanceof Error ? error.message : String(error) });
    }
  }

  /** Calls for tools this worker just dropped are answered instead of waiting for a timeout. */
  private drainDropped(before: string[]): void {
    const board = this.bus.board("RunToolJob");
    for (const name of before) {
      if (this.own.has(name)) continue;
      while (true) {
        const job = board.claim(this.worker_name, (input) => input.call.name === name);
        if (!job) break;
        board.submit(this.worker_name, job.id, { error: `tool ${name} is no longer available` });
      }
    }
  }

  async stop(): Promise<void> {
    await Promise.all(this.pending);
    await Promise.all([...this.connections.values()].map((connection) => connection.close().catch(() => {})));
    const before = [...this.own.keys()];
    this.connections.clear();
    this.inject();
    this.drainDropped(before);
  }

  private inject(): void {
    const tools = [...this.connections.values()].flatMap((connection) => connection.tools);
    // A name clash throws here, leaving the previous map and catalog untouched.
    this.bus.tools.replaceSource("mcp", tools);
    this.own.clear();
    for (const tool of tools) this.own.set(tool.name, tool);
  }
}

export async function connectMcpServer(config: McpServerConfig, workspace: string): Promise<McpConnection> {
  const client = new Client({ name: "magi", version: "0.2.0" });
  const headers = config.headers ?? {};
  const transport = config.connection_type === "stdio"
    ? new StdioClientTransport({ command: config.command ?? "", args: config.args ?? [], cwd: workspace, env: { ...cleanEnvironment(), ...(config.env ?? {}) } })
    : config.connection_type === "sse"
      ? new SSEClientTransport(new URL(config.url ?? ""), { requestInit: { headers } })
      : new StreamableHTTPClientTransport(new URL(config.url ?? ""), { requestInit: { headers } });
  try {
    await deadline(client.connect(transport), (config.connect_timeout ?? 10) * 1_000, "MCP connection timed out");
    const listed = await deadline(client.listTools(), (config.connect_timeout ?? 10) * 1_000, "MCP tools/list timed out");
    const tools: ExecutableTool[] = listed.tools.map((tool) => ({
      name: `${config.name}__${tool.name}`,
      description: tool.description || "(no description provided by MCP server)",
      input_schema: (tool.inputSchema ?? { type: "object", properties: {} }) as Record<string, unknown>,
      async run(args) {
        const result = await deadline(client.callTool({ name: tool.name, arguments: args }), (config.execute_timeout ?? 60) * 1_000, `MCP tool ${tool.name} timed out`);
        const content = (result.content ?? []).map((item) => item.type === "text" ? item.text : JSON.stringify(item)).join("\n");
        if (result.isError) throw new Error(content || `MCP tool ${tool.name} failed`);
        return content;
      },
    }));
    return { tools, close: () => client.close() };
  } catch (error) {
    await client.close().catch(() => {});
    throw error;
  }
}

// An MCP server runs as its own process: it gets this environment plus the
// server's configured one. MAGI's own configuration never comes from here.
function cleanEnvironment(): Record<string, string> {
  return Object.fromEntries(Object.entries(process.env).filter((entry): entry is [string, string] => entry[1] !== undefined));
}

async function deadline<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  return Promise.race([promise, Bun.sleep(timeoutMs).then(() => { throw new Error(message); })]);
}
