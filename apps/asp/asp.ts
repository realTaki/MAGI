/**
 * One ASP process: its BUS, and the workers on it.
 *
 * This file is the only one allowed to know which workers exist.
 * ManagerWorker runs them. The relay is `@magi/asp-server`.
 */

import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { Bus } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";

import { AspServer, ASP_HANDLE, defaultDatabasePath, intranetBaseUrl, type CreateAppOptions } from "@magi/asp-server";

export type { CreateAppOptions };

export class Asp {
  readonly bus: Bus;
  readonly server: AspServer;
  private readonly manager: ManagerWorker;

  constructor(options: CreateAppOptions = {}) {
    const databasePath = options.databasePath ?? defaultDatabasePath();
    this.bus = new Bus(ASP_HANDLE, options.workspace ?? dirname(databasePath));
    this.server = new AspServer(this.bus, { ...options, databasePath });
    this.manager = new ManagerWorker(this.bus, [
      this.server,
    ]);
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
  console.error(`[asp] listening on ${asp.origin}`);
  try {
    await new Promise<void>((resolve) => {
      process.once("SIGINT", resolve);
      process.once("SIGTERM", resolve);
    });
  } finally { await asp.stop(); }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
