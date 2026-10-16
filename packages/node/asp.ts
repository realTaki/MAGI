/**
 * Casper's workers. The eva launcher starts this inside the child process.
 * Casper speaks on the local relay through AspWorker, the same path as any node.
 */

import { dirname, join } from "node:path";
import { ASP_MAGI_HANDLE, ASP_MAGI_NICKNAME, MAGI_CONTACT_ID, Bus } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";
import { AgentWorker } from "@magi/agent/worker.js";
import { ToolsWorker } from "@magi/built-in-tools/worker.js";
import { ContactsWorker } from "@magi/contacts/worker.js";
import { MemoryWorker } from "@magi/memory/worker.js";
import { ProvidersWorker } from "@magi/providers/worker.js";
import type { LLMClient } from "@magi/providers/client.js";
import { SkillWorker } from "@magi/skills/worker.js";
import { AspWorker } from "@magi/channel-asp/worker.js";
import { TelegramWorker } from "@magi/channel-telegram/worker.js";

import { AspServer, defaultDatabasePath, type CreateAppOptions } from "@magi/asp-server";

export type { CreateAppOptions };
export type AspOptions = CreateAppOptions & { client?: LLMClient };

export class Asp {
  readonly bus: Bus;
  readonly server: AspServer;
  private readonly manager: ManagerWorker;

  constructor(options: AspOptions = {}) {
    const databasePath = options.databasePath ?? defaultDatabasePath();
    this.bus = new Bus(ASP_MAGI_HANDLE, options.workspace ?? dirname(databasePath));
    this.bus.contacts.update(MAGI_CONTACT_ID, { nickname: ASP_MAGI_NICKNAME });
    this.server = new AspServer(this.bus, { ...options, databasePath });
    this.manager = new ManagerWorker(this.bus, [
      new ContactsWorker(this.bus),
      new SkillWorker(this.bus),
      new AgentWorker(this.bus),
      new ToolsWorker(this.bus),
      new MemoryWorker(this.bus),
      new ProvidersWorker(this.bus, options.client),
      this.server,
      new AspWorker(this.bus),
      // Telegram stays off until this workspace has a bot token.
      new TelegramWorker(this.bus),
    ], {
      drain: ["agent"],
      build: { app: "asp", script: "build" },
    });
  }

  /** Null when the workers that should be up have nothing wrong. */
  healthy(): string | null { return this.manager.healthy(); }

  get origin(): string { return this.server.origin; }
  get database() { return this.server.database; }
  get store() { return this.server.store; }
  get transport() { return this.server.transport; }
  get operatorHandle() { return this.server.operatorHandle; }
  get operatorToken() { return this.server.operatorToken; }

  async start(): Promise<void> {
    await this.manager.start();
    if (this.manager.isRunning(this.server.worker_name)) return;
    await this.manager.stop();
    throw new Error(`worker "${this.server.worker_name}" could not start`);
  }

  async stop(): Promise<void> {
    await this.manager.stop();
    this.bus.close();
  }
}

export async function runAsp(): Promise<void> {
  const { aspBase } = await import("@magi/runtime/asp.js");
  const { healthyOrExit, untilHealthy } = await import("@magi/manager");
  if (process.env.MAGI_HEALTH === "1") {
    const workspace = process.env.MAGI_HEALTH_WORKSPACE;
    const asp = new Asp({
      workspace,
      databasePath: workspace ? join(workspace, "asp.sqlite") : undefined,
      host: "127.0.0.1",
      port: 0,
    });
    await asp.start();
    const problem = await untilHealthy(() => asp.healthy());
    await asp.stop();
    if (problem) {
      console.error(problem);
      process.exitCode = 1;
    }
    return;
  }
  const base = new URL(aspBase());
  const asp = new Asp({
    aspBase: base.href,
    requestShutdown: () => process.kill(process.pid, "SIGTERM"),
    host: base.hostname,
    port: Number(base.port || "80"),
  });
  await asp.start();
  await healthyOrExit(() => asp.healthy());
  process.send?.({ v: 1, type: "ready" });
  console.error(`[asp] listening on ${asp.origin}`);
  try {
    await new Promise<void>((resolve) => {
      process.once("SIGINT", resolve);
      process.once("SIGTERM", resolve);
      process.on("message", (message) => {
        if ((message as { type?: string })?.type === "shutdown") resolve();
      });
    });
  } finally { await asp.stop(); }
}
