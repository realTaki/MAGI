/**
 * One ASP process: its BUS, and the server worker on it.
 *
 * Same shape as `apps/eva/eva.ts`. This file is the supervisor. The relay itself
 * is a worker (`AspServer`) that later moves to `packages/`.
 */

import { dirname } from "node:path";
import { Bus } from "@magi/bus";

import { defaultDatabasePath } from "./db/database.ts";
import { AspServer, intranetBaseUrl, type CreateAppOptions } from "./server/app.ts";
import { ASP_HANDLE } from "./server/relayWorker.ts";

export class Asp {
  readonly worker_name = "manager";
  readonly bus: Bus;
  readonly server: AspServer;

  constructor(options: CreateAppOptions = {}) {
    const databasePath = options.databasePath ?? defaultDatabasePath();
    this.bus = new Bus(ASP_HANDLE, options.workspace ?? dirname(databasePath));
    this.server = new AspServer(this.bus, { ...options, databasePath });
  }

  get origin(): string { return this.server.origin; }
  get database() { return this.server.database; }
  get store() { return this.server.store; }
  get transport() { return this.server.transport; }
  get operatorHandle() { return this.server.operatorHandle; }
  get operatorToken() { return this.server.operatorToken; }

  start(host = "127.0.0.1", port = 0): Promise<void> {
    return this.server.start(host, port);
  }

  async stop(): Promise<void> {
    await this.server.stop();
    this.bus.close();
  }
}

export type { CreateAppOptions };
export { intranetBaseUrl };

export function createApp(options: CreateAppOptions = {}): Asp {
  return new Asp(options);
}
