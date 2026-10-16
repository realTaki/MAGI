import { realpathSync } from "node:fs";
/**
 * One ASP process: its BUS, and the workers on it.
 *
 * This file is the only one allowed to know which workers exist.
 * ManagerWorker runs them. The relay is `@magi/asp-server`.
 *
 * The BUS is owned by the host MAGI (`@eva-001.magi`, Casper). Shell still
 * keys this process as `@asp.magi` for rebuild. Casper speaks on ASP through
 * AspWorker over the local relay, the same path as any MAGI.
 */

import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
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

import { AspServer, defaultDatabasePath, intranetBaseUrl, type CreateAppOptions } from "@magi/asp-server";

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
    ], {
      drain: ["agent"],
    });
  }

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

async function main(): Promise<void> {
  const host = process.env.MAGI_ASP_HOST ?? "127.0.0.1";
  const port = Number(process.env.MAGI_ASP_PORT ?? "42069");
  const asp = new Asp({
    aspBase: intranetBaseUrl(),
    requestShutdown: () => process.kill(process.pid, "SIGTERM"),
    host,
    port,
  });
  await asp.start();
  process.send?.({ type: "ready" });
  console.error(`[asp] listening on ${asp.origin}`);
  try {
    await new Promise<void>((resolve) => {
      process.once("SIGINT", resolve);
      process.once("SIGTERM", resolve);
    });
  } finally { await asp.stop(); }
}

if (process.argv[1] && realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1])) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
