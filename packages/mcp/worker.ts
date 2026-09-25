import { Client, SSEClientTransport, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";
import { setTimeout as sleep } from "node:timers/promises";
import { BaseWorker, type Bus, type ExecutableTool, type McpServerConfig, type ToolSource } from "@magi/bus";
import { mcpTools } from "./tools.js";

export type McpConnection = { tools: ExecutableTool[]; close(): Promise<void> };
export type McpConnector = (config: McpServerConfig, workspace: string) => Promise<McpConnection>;

export class McpWorker extends BaseWorker {
  readonly worker_name = "mcp";
  private readonly connections = new Map<string, McpConnection>();
  /**
   * MCP's own control surface — `mcp_server` — travels with the worker instead of
   * sitting in the builtin catalog: the tool exists exactly as long as this worker
   * runs, so a stopped MCP cannot leave the model holding a tool nobody answers.
   */
  private readonly control: ExecutableTool[];
  private readonly pending = new Set<Promise<void>>();
  private started = false;
  /** The catalog asks this every time, so a connection that comes or goes shows up at once. */
  private readonly provider: ToolSource = () => this.tools();

  constructor(bus: Bus, private readonly connector: McpConnector = connectMcpServer) {
    super(bus);
    this.control = mcpTools(bus);
    bus.tools.registerSource("mcp", this.provider);
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    await Promise.all(this.bus.mcpServers.list().filter((server) => server.enabled !== false).map(async (server) => {
      try { this.connections.set(server.name, await this.connector(server, this.bus.workspace)); }
      catch (error) { this.bus.publishNotice(`[mcp] ${server.name}: ${message(error)}`); }
    }));
    // A name clash here would make the whole catalog unreadable, and a boot is no
    // place to fail a MAGI over it: tell the operator, keep the connections, stay running.
    try { this.revalidate(); }
    catch (error) { this.bus.publishNotice(`[mcp] catalog: ${message(error)}`); }
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
      const before = this.tools().map((tool) => tool.name);
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
      this.revalidate();
      this.drainDropped(before);
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  private async pollToolCall(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const names = new Set(this.tools().map((tool) => tool.name));
    const job = board.claim(this.worker_name, (input) => names.has(input.call.name));
    if (!job) return false;
    const task = this.callTool(job.id, job.input.call.name, job.input.call.arguments);
    this.pending.add(task);
    void task.finally(() => this.pending.delete(task));
    return true;
  }

  private async callTool(id: number, name: string, args: Record<string, unknown>): Promise<void> {
    const board = this.bus.board("RunToolJob");
    try {
      const tool = this.tools().find((candidate) => candidate.name === name);
      if (!tool) throw new Error(`unknown tool ${name}`);
      board.submit(this.worker_name, id, { output: { content: await tool.run(args) } });
    } catch (error) {
      board.submit(this.worker_name, id, { error: error instanceof Error ? error.message : String(error) });
    }
  }

  /** Calls for tools this worker just dropped are answered instead of waiting for a timeout. */
  private drainDropped(before: string[]): void {
    const board = this.bus.board("RunToolJob");
    const live = new Set(this.tools().map((tool) => tool.name));
    for (const name of before) {
      if (live.has(name)) continue;
      while (true) {
        const job = board.claim(this.worker_name, (input) => input.call.name === name);
        if (!job) break;
        board.submit(this.worker_name, job.id, { error: `tool ${name} is no longer available` });
      }
    }
  }

  async stop(): Promise<void> {
    // A stopped MCP worker can be started again: the manager owns its lifecycle now.
    this.started = false;
    await Promise.all(this.pending);
    await Promise.all([...this.connections.values()].map((connection) => connection.close().catch(() => {})));
    const before = this.tools().map((tool) => tool.name);
    this.connections.clear();
    this.revalidate();
    this.drainDropped(before);
  }

  private tools(): ExecutableTool[] {
    return [
      ...this.control,
      ...[...this.connections.values()].flatMap((connection) => connection.tools),
    ];
  }

  /** Re-register so a name clash fails here, at the change, instead of in the middle of a turn. */
  private revalidate(): void {
    this.bus.tools.registerSource("mcp", this.provider);
  }
}

export async function connectMcpServer(config: McpServerConfig, workspace: string): Promise<McpConnection> {
  const client = new Client({ name: "magi", version: "0.2.0" });
  const headers = config.headers ?? {};
  const transport = config.connection_type === "stdio"
    ? new StdioClientTransport({ command: config.command ?? "", args: config.args ?? [], cwd: workspace, env: { ...inheritedEnvironment(), ...(config.env ?? {}) } })
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

// An MCP server runs as its own process: it inherits this process's environment
// plus the server's configured one. This is not a boundary and must not read like
// one — the child runs as the same user and can read the same files — and it is
// the environment those servers need to start at all (PATH, HOME, proxies, CAs).
function inheritedEnvironment(): Record<string, string> {
  return Object.fromEntries(Object.entries(process.env).filter((entry): entry is [string, string] => entry[1] !== undefined));
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function deadline<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  return Promise.race([promise, sleep(timeoutMs).then(() => { throw new Error(message); })]);
}
