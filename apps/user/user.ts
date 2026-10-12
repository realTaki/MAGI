/**
 * One operator process: its BUS, and the workers on it.
 *
 * Same shape as `apps/eva/eva.ts`. This file chooses the workers.
 * ManagerWorker runs them. The backend is `@magi/userend`.
 */

import { join } from "node:path";
import { Bus } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";
import { AspProcessWorker, UserendWorker, USER_HANDLE } from "@magi/userend";

type UserContext = ConstructorParameters<typeof UserendWorker>[1];

export class User {
  readonly bus: Bus;
  readonly worker: UserendWorker;
  readonly aspProcess: AspProcessWorker;
  private readonly manager: ManagerWorker;
  private ready: Promise<void> | null = null;
  private stopping: Promise<void> | null = null;

  constructor(context: UserContext) {
    const workspace = join(context.paths.home, ".magi", "user");
    this.bus = new Bus(USER_HANDLE, workspace);
    this.aspProcess = new AspProcessWorker(this.bus, {
      sourceCheckout: context.paths.checkout,
      node: context.tools.node,
      env: context.tools.env,
      spawnProcess: context.spawn,
    });
    this.worker = new UserendWorker(this.bus, { ...context, aspProcess: this.aspProcess });
    this.manager = new ManagerWorker(this.bus, [this.worker, this.aspProcess], {
      drain: ["userend"],
    });
  }

  start(): Promise<void> {
    this.ready ??= this.manager.start();
    return this.ready;
  }

  async stop(shutdown = false): Promise<void> {
    if (shutdown) {
      this.worker.requestShutdown();
      this.aspProcess.requestShutdown();
    }
    if (this.stopping) {
      await this.stopping;
      if (shutdown) {
        await this.worker.stop();
        await this.aspProcess.stop();
      }
      return;
    }
    this.stopping = this.stopOnce();
    await this.stopping;
  }

  private async stopOnce(): Promise<void> {
    if (this.ready) await this.ready.catch(() => undefined);
    await this.manager.stop();
    this.bus.close();
  }
}

export function createLocalApi(context: ConstructorParameters<typeof UserendWorker>[1]) {
  const user = new User(context);
  const ready = user.start();
  let startError: unknown;
  void ready.catch((error: unknown) => {
    startError = error;
  });
  const methods = user.worker.methods;
  const whenReady = async <T>(run: () => T | Promise<T>): Promise<T> => {
    try {
      await ready;
    } catch (error) {
      throw startError ?? error;
    }
    return await run();
  };
  return {
    ...methods,
    prepare: (progress: Parameters<typeof methods.prepare>[0]) =>
      whenReady(() => methods.prepare(progress)),
    start: (progress: Parameters<typeof methods.start>[0]) =>
      whenReady(() => methods.start(progress)),
    dispose: () => user.stop(),
    shutdown: () => user.stop(true),
  };
}
