import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { Bus } from "./bus/index.js";
import { AgentWorker } from "./agent/worker.js";
import { ProvidersWorker } from "./providers/worker.js";
import type { LLMClient } from "./providers/client.js";
import { ToolsWorker } from "./tools/worker.js";
import type { Tool } from "./tools/registry.js";
import { CliWorker } from "./channels/cli/worker.js";
import { AspWorker } from "./channels/asp/worker.js";
import { TelegramWorker } from "./channels/telegram/worker.js";
import { TaskWorker } from "./channels/tasks/worker.js";
import { McpWorker, type McpConnector } from "./mcp/worker.js";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class Magi {
  readonly bus: Bus;
  readonly agent: AgentWorker;
  readonly providers: ProvidersWorker;
  readonly tools: ToolsWorker;
  readonly cli: CliWorker;
  readonly asp: AspWorker | null;
  readonly telegram: TelegramWorker | null;
  readonly tasks: TaskWorker;
  readonly mcp: McpWorker;
  private running = false;
  private loops: Promise<void>[] = [];

  constructor(handle: string, options: { workspace?: string; migrationSource?: string | null; client?: LLMClient; tools?: Tool[]; deliver?: (text: string) => void; asp?: { base: string; token: string }; telegram?: { token: string; apiBase?: string }; mcpConnector?: McpConnector } = {}) {
    this.bus = new Bus(handle, options.workspace, options.migrationSource);
    this.tools = new ToolsWorker(this.bus, options.tools);
    this.agent = new AgentWorker(this.bus);
    this.providers = new ProvidersWorker(this.bus, options.client);
    this.cli = new CliWorker(this.bus, options.deliver);
    this.asp = options.asp ? new AspWorker(this.bus, options.asp.base, options.asp.token) : null;
    const telegramToken = options.telegram?.token ?? this.bus.getSetting("telegram.bot_token") ?? undefined;
    this.telegram = telegramToken ? new TelegramWorker(this.bus, telegramToken, options.telegram?.apiBase) : null;
    this.tasks = new TaskWorker(this.bus);
    this.mcp = new McpWorker(this.bus, options.mcpConnector);
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.telegram?.start();
    void this.mcp.start();
    this.loops = [this.agent, this.tools, this.providers, this.cli, this.asp, this.telegram, this.tasks, this.mcp]
      .filter((worker) => worker !== null)
      .map((worker) => this.runWorkerLoop(worker));
  }

  private async runWorkerLoop(worker: { worker_name: string; poll(): Promise<boolean> }): Promise<void> {
    while (this.running) {
      let worked = false;
      try { worked = await worker.poll(); }
      catch (error) { console.error(`${worker.worker_name}:`, error); }
      if (!worked) await sleep(20);
    }
  }

  async chat(text: string, address = "terminal"): Promise<number> {
    if (!this.running) throw new Error("MAGI is not running");
    const id = this.bus.publishChat({ text, channel: "cli", delivery_address: address });
    while (this.running) {
      if (this.bus.board("ChatNotify").result(id)) return id;
      await sleep(20);
    }
    throw new Error("MAGI stopped before completing the turn");
  }

  async stop(): Promise<void> {
    await this.agent.drain();
    this.running = false;
    await Promise.all(this.loops);
    this.loops = [];
    this.asp?.close();
    await this.telegram?.stop();
    await this.mcp.stop();
    await this.tools.stop();
    this.bus.close();
  }
}

async function main(): Promise<void> {
  const handle = process.argv[2];
  if (!handle) throw new Error("usage: bun run start -- @handle.magi [asp-base asp-token]");
  const [base, token] = process.argv.slice(3);
  if ((base && !token) || (!base && token)) throw new Error("ASP base and token must be supplied together");
  const magi = new Magi(handle, { asp: base && token ? { base, token } : undefined });
  magi.start();
  if (magi.asp) {
    try {
      await magi.asp.connect();
      await new Promise<void>((resolve) => {
        process.once("SIGINT", resolve);
        process.once("SIGTERM", resolve);
      });
    } finally { await magi.stop(); }
    return;
  }
  const input = createInterface({ input: stdin, output: stdout });
  try {
    while (true) {
      const line = await input.question("> ");
      if (line.trim() === "/exit") break;
      if (line.trim()) await magi.chat(line);
    }
  } finally { input.close(); await magi.stop(); }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
