/**
 * One operator process: its BUS, and the workers on it.
 *
 * Same shape as `apps/eva/eva.ts`. This file chooses the workers.
 * ManagerWorker runs them. The backend is `@magi/webapp`.
 */

import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { Bus } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";
import { AspProcessWorker, WebappWorker, USER_HANDLE } from "@magi/webapp";

type UserContext = ConstructorParameters<typeof WebappWorker>[1];

export class User {
  readonly bus: Bus;
  readonly worker: WebappWorker;
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
      startProcess: context.startProcess,
    });
    this.worker = new WebappWorker(this.bus, { ...context, aspProcess: this.aspProcess });
    this.manager = new ManagerWorker(this.bus, [this.worker, this.aspProcess], {
      drain: ["webapp"],
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

export function createLocalApi(context: ConstructorParameters<typeof WebappWorker>[1]) {
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

function requireEnv(name: string): string {
  const value = process.env[name];
  if (value === undefined || value === "") throw new Error(`${name} is not set`);
  return value;
}

async function main(): Promise<void> {
  const { connectHost } = await import("./host.js");
  const host = connectHost(process.stdin, process.stdout);
  const checkout = requireEnv("MAGI_CHECKOUT");
  const api = createLocalApi({
    paths: {
      checkout,
      appCheckout: process.env.MAGI_APP_CHECKOUT || checkout,
      home: requireEnv("MAGI_HOME"),
      userData: requireEnv("MAGI_USER_DATA"),
    },
    repository: requireEnv("MAGI_REPOSITORY"),
    managed: process.env.MAGI_MANAGED === "1",
    shellUpdate: {
      packaged: process.env.MAGI_PACKAGED === "1",
      currentVersion: process.env.MAGI_SHELL_VERSION ?? "",
      install: (installer: string) => host.installShellUpdate({ installer }),
    },
    tools: {
      git: requireEnv("MAGI_GIT"),
      node: requireEnv("MAGI_NODE"),
      npm: requireEnv("MAGI_NPM"),
      env: process.env,
    },
    emit: (event: string, payload: unknown) => {
      void host.emit({ event, payload });
    },
    openExternal: (url: string) => host.openExternal({ url }),
    copy: (text: string) => {
      void host.copy({ text });
    },
    startProcess: host.startProcess,
  });
  const handlers: Record<string, (params: unknown) => unknown> = {};
  for (const [name, value] of Object.entries(api)) {
    if (typeof value !== "function") continue;
    handlers[name] = (params) => value(params);
  }
  handlers.prepare = () =>
    api.prepare((message: string, percent: number) => {
      void host.progress({ message, percent });
    });
  handlers.start = () =>
    api.start((message: string, percent: number) => {
      void host.progress({ message, percent });
    });
  host.serve(handlers);
  try {
    await new Promise<void>((resolve) => {
      process.once("SIGINT", resolve);
      process.once("SIGTERM", resolve);
    });
  } finally {
    await api.shutdown();
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
