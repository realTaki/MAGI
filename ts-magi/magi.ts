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

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class Magi {
  readonly bus: Bus;
  readonly agent: AgentWorker;
  readonly providers: ProvidersWorker;
  readonly tools: ToolsWorker;
  readonly cli: CliWorker;
  private running = false;
  private loop: Promise<void> | null = null;

  constructor(handle: string, options: { workspace?: string; client?: LLMClient; tools?: Tool[]; deliver?: (text: string) => void } = {}) {
    this.bus = new Bus(handle, options.workspace);
    this.tools = new ToolsWorker(this.bus, options.tools);
    this.agent = new AgentWorker(this.bus, () => this.tools.catalog());
    this.providers = new ProvidersWorker(this.bus, options.client);
    this.cli = new CliWorker(this.bus, options.deliver);
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.loop = this.runLoop();
  }

  private async runLoop(): Promise<void> {
    while (this.running) {
      let worked = false;
      for (const worker of [this.agent, this.tools, this.providers, this.cli]) {
        try { worked = await worker.poll() || worked; }
        catch (error) { console.error(`${worker.worker_name}:`, error); }
      }
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
    this.running = false;
    await this.loop;
    await this.agent.drain();
    this.bus.close();
  }
}

async function main(): Promise<void> {
  const handle = process.argv[2];
  if (!handle) throw new Error("usage: npm run start -- @handle.magi");
  const magi = new Magi(handle, { workspace: process.env.MAGI_WORKSPACE });
  magi.start();
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
