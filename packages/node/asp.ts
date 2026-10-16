/**
 * Casper's workers. The eva launcher starts this inside the child process.
 * Casper speaks on the local relay through AspWorker, the same path as any node.
 */

import { dirname } from "node:path";
import { ASP_MAGI_HANDLE, ASP_MAGI_NICKNAME, MAGI_CONTACT_ID, Bus } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";
import type { LLMClient } from "@magi/providers/client.js";
import { AspServer, defaultDatabasePath, type CreateAppOptions } from "@magi/asp-server";
import { aspWorkers } from "./roles.js";

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
    this.manager = new ManagerWorker(this.bus, aspWorkers(this.bus, this.server, options.client), {
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

