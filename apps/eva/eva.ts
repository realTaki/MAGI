import { realpathSync } from "node:fs";
/**
 * One long-lived MAGI: its BUS, and the workers on it.
 *
 * This file is the only one allowed to know every module, so it owns which workers
 * exist and, by construction order, the order their blocks appear in the system
 * context. ManagerWorker runs them.
 */

import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { Bus, messageDelivery } from "@magi/bus";
import { ManagerWorker, healthyOrExit, untilHealthy } from "@magi/manager";
import { AgentWorker } from "@magi/agent/worker.js";
import { ProvidersWorker } from "@magi/providers/worker.js";
import type { LLMClient } from "@magi/providers/client.js";
import { ToolsWorker, type Tool } from "@magi/built-in-tools/worker.js";
import { ContactsWorker } from "@magi/contacts/worker.js";
import { MemoryWorker } from "@magi/memory/worker.js";
import { CliWorker } from "@magi/channel-cli/worker.js";
import { AspWorker } from "@magi/channel-asp/worker.js";
import { TelegramWorker } from "@magi/channel-telegram/worker.js";
import { TaskWorker } from "@magi/channel-tasks/worker.js";
import { McpWorker, type McpConnector } from "@magi/mcp/worker.js";
import { SkillWorker } from "@magi/skills/worker.js";

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

export class Magi {
  readonly bus: Bus;
  private readonly manager: ManagerWorker;

  constructor(handle: string, options: MagiOptions = {}) {
    this.bus = new Bus(handle, options.workspace);
    if (options.asp) {
      this.bus.settings.set("asp.base", options.asp.base);
      this.bus.settings.set("asp.token", options.asp.token);
    }
    this.manager = new ManagerWorker(this.bus, [
      // Contacts first: Identity leads the system context. A worker registers the
      // blocks it answers for as it is built, so this order is also prompt order.
      new ContactsWorker(this.bus),
      new SkillWorker(this.bus),
      new AgentWorker(this.bus),
      new ToolsWorker(this.bus, options.tools),
      new MemoryWorker(this.bus),
      new ProvidersWorker(this.bus, options.client),
      new CliWorker(this.bus, options.deliver),
      new TaskWorker(this.bus),
      new McpWorker(this.bus, options.mcpConnector),
      new AspWorker(this.bus, options.asp),
      new TelegramWorker(this.bus, options.telegram),
    ], {
      // The agent has to *finish* rather than be stopped: a turn it already accepted
      // may still need a provider to answer and a channel to deliver.
      drain: ["agent"],
      build: { app: "eva", script: "build" },
    });
  }

  /** Null when the workers that should be up have nothing wrong. */
  healthy(): string | null { return this.manager.healthy(); }

  start(): Promise<void> { return this.manager.start(); }

  async stop(): Promise<void> {
    await this.manager.stop();
    this.bus.close();
  }

  async chat(text: string, address = "terminal"): Promise<number> {
    if (!this.manager.up) throw new Error("MAGI is not running");
    const chat = this.bus.chats.forChannel("cli", address);
    // The terminal is the operator of a hand-run MAGI, and their first message is what
    // establishes where this workspace reports trouble.
    if (messageDelivery.homeChat(this.bus) === null) messageDelivery.setHomeChat(this.bus, chat.id);
    const id = messageDelivery.send(this.bus, chat.id, text);
    while (this.manager.up) {
      if (this.bus.board("MessageDeliveryJob").result(id)) return id;
      await sleep(20);
    }
    throw new Error("MAGI stopped before completing the turn");
  }
}

async function main(): Promise<void> {
  if (process.env.MAGI_HEALTH === "1") {
    await runHealth();
    return;
  }
  const handle = process.argv[2];
  if (!handle) throw new Error("usage: npm start -- @handle.magi [asp-base asp-token] [--workspace path]");
  const positional: string[] = [];
  let workspace: string | undefined;
  for (let index = 3; index < process.argv.length; index += 1) {
    const argument = process.argv[index];
    if (argument !== "--workspace") {
      positional.push(argument);
      continue;
    }
    workspace = process.argv[index + 1];
    if (!workspace) throw new Error("--workspace needs a path");
    index += 1;
  }
  if (positional.length > 2) throw new Error("usage: npm start -- @handle.magi [asp-base asp-token] [--workspace path]");
  const [base, token] = positional;
  if ((base && !token) || (!base && token)) throw new Error("ASP base and token must be supplied together");
  const magi = new Magi(handle, { workspace, asp: base && token ? { base, token } : undefined });
  let stopService: (() => void) | undefined;
  const shutdown = base && token ? new Promise<void>((resolve) => {
    stopService = resolve;
    process.on("message", (message) => {
      if ((message as { type?: string })?.type === "shutdown") resolve();
    });
  }) : null;
  await magi.start();
  await healthyOrExit(() => magi.healthy());
  // Until startup finishes, keep default signal termination: a stalled start
  // cannot drain workers and must still be stoppable by Shell's ready timeout.
  if (stopService) { process.once("SIGINT", stopService); process.once("SIGTERM", stopService); }
  process.send?.({ type: "ready", lifecycle: shutdown ? ["shutdown"] : [] });
  // With ASP the process is a service: it stays up until it is asked to stop.
  // Without one it is a prompt, which is how a MAGI is tried out by hand.
  if (base && token) {
    try {
      await shutdown;
    } finally {
      process.send?.({ type: "stopping" });
      await magi.stop();
      if (process.connected) process.disconnect();
    }
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

async function runHealth(): Promise<void> {
  const magi = new Magi(process.argv[2] || "@health.magi", { workspace: process.env.MAGI_HEALTH_WORKSPACE });
  await magi.start();
  const problem = await untilHealthy(() => magi.healthy());
  await magi.stop();
  if (problem) {
    console.error(problem);
    process.exitCode = 1;
  }
}

if (process.argv[1] && realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1])) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
