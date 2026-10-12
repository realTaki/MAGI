/**
 * One operator process: its BUS, and the workers on it.
 *
 * Same shape as `apps/eva/eva.ts`. This file chooses the workers.
 * ManagerWorker runs them. The backend is `@magi/userend`.
 */

import { join } from "node:path";
import { Bus } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";
import { UserendWorker, USER_HANDLE } from "@magi/userend";

export class User {
  readonly bus: Bus;
  readonly worker: UserendWorker;
  private readonly manager: ManagerWorker;

  constructor(context: ConstructorParameters<typeof UserendWorker>[1]) {
    const workspace = join(context.paths.home, ".magi", "user");
    this.bus = new Bus(USER_HANDLE, workspace);
    this.worker = new UserendWorker(this.bus, context);
    this.manager = new ManagerWorker(this.bus, [this.worker]);
  }

  start(): Promise<void> { return this.manager.start(); }

  async stop(): Promise<void> {
    await this.manager.stop();
    this.bus.close();
  }
}

export function createLocalApi(context: ConstructorParameters<typeof UserendWorker>[1]) {
  const user = new User(context);
  void user.start();
  const methods = user.worker.methods;
  const shutdown = methods.shutdown;
  return {
    ...methods,
    dispose: () => {
      void user.stop();
    },
    shutdown: () => {
      shutdown();
      void user.stop();
    },
  };
}
