/**
 * One ASP process: its BUS, and the workers on it.
 *
 * Same shape as `apps/eva/eva.ts`. This file chooses the workers.
 * ManagerWorker runs them. The relay is `@magi/asp-server`.
 */

import { dirname } from "node:path";
import { Bus } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";

import { AspServer, ASP_HANDLE, defaultDatabasePath, intranetBaseUrl, type CreateAppOptions } from "@magi/asp-server";

export class Asp {
  readonly bus: Bus;
  readonly server: AspServer;
  private readonly manager: ManagerWorker;

  constructor(options: CreateAppOptions = {}) {
    const databasePath = options.databasePath ?? defaultDatabasePath();
    this.bus = new Bus(ASP_HANDLE, options.workspace ?? dirname(databasePath));
    this.server = new AspServer(this.bus, { ...options, databasePath });
    this.manager = new ManagerWorker(this.bus, [this.server]);
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

export type { CreateAppOptions };
export { intranetBaseUrl };

export function createApp(options: CreateAppOptions = {}): Asp {
  return new Asp(options);
}
