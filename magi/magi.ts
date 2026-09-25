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
import { ManagerWorker } from "./manager/worker.js";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export type MagiOptions = {
  workspace?: string;
  client?: LLMClient;
  tools?: Tool[];
  deliver?: (text: string) => void;
  asp?: { base: string; token: string };
  telegram?: { token: string; apiBase?: string };
  mcpConnector?: McpConnector;
};

/**
 * One long-lived MAGI: its BUS, and the worker that runs the other workers.
 *
 * This file is the only one allowed to know every module, so it is also the only one
 * that names them — a worker added here is a worker the manager can start and stop.
 */
export class Magi {
  readonly bus: Bus;
  readonly manager: ManagerWorker;
  private running = false;
  private loop: Promise<void> | null = null;

  constructor(handle: string, options: MagiOptions = {}) {
    this.bus = new Bus(handle, options.workspace);
    this.manager = new ManagerWorker(this.bus, [
      new AgentWorker(this.bus),
      new ToolsWorker(this.bus, options.tools),
      new ProvidersWorker(this.bus, options.client),
      new CliWorker(this.bus, options.deliver),
      new TaskWorker(this.bus),
      new McpWorker(this.bus, options.mcpConnector),
      ...(options.asp ? [new AspWorker(this.bus, options.asp.base, options.asp.token)] : []),
      new TelegramWorker(this.bus, options.telegram),
    ]);
  }

  async start(): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.loop = this.runManager();
    await this.manager.start();
  }

  private async runManager(): Promise<void> {
    while (this.running) {
      let worked = false;
      try { worked = await this.manager.poll(); }
      catch (error) { console.error("manager:", error); }
      if (!worked && this.running) await sleep(20);
    }
  }

  async chat(text: string, address = "terminal"): Promise<number> {
    if (!this.running) throw new Error("MAGI is not running");
    const conversation = this.bus.conversations.forOperator("cli", address);
    const id = this.bus.publishChat({ conversation_id: conversation.id, text });
    while (this.running) {
      if (this.bus.board("ChatNotify").result(id)) return id;
      await sleep(20);
    }
    throw new Error("MAGI stopped before completing the turn");
  }

  async stop(): Promise<void> {
    this.running = false;
    await this.loop;
    this.loop = null;
    await this.manager.stop();
    this.bus.close();
  }
}

async function main(): Promise<void> {
  const handle = process.argv[2];
  if (!handle) throw new Error("usage: bun run start -- @handle.magi [asp-base asp-token]");
  const [base, token] = process.argv.slice(3);
  if ((base && !token) || (!base && token)) throw new Error("ASP base and token must be supplied together");
  const magi = new Magi(handle, { asp: base && token ? { base, token } : undefined });
  await magi.start();
  // With ASP the process is a service: it stays up until it is asked to stop.
  // Without one it is a prompt, which is how a MAGI is tried out by hand.
  if (base && token) {
    try {
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
