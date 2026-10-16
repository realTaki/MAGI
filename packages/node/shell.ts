/**
 * Balthasar's workers, including the UI backend. The eva launcher starts this
 * inside the child process. Electron is the window; this process is the BUS.
 */

import { existsSync, chmodSync } from "node:fs";
import { dirname, join } from "node:path";
import { magiHome } from "@magi/runtime/home.js";
import type { ProcessSpec } from "./host.js";

function prepareShellWorkspace(home: string): string {
  const root = process.env.MAGI_HOME?.trim() ? magiHome() : join(home, ".magi");
  return join(root, "shell");
}

function secureShellDatabases(workspace: string): void {
  for (const database of ["memories/books.db", "logs/jobs.db"]) {
    for (const suffix of ["", "-wal", "-shm"]) {
      const file = join(workspace, `${database}${suffix}`);
      if (existsSync(file)) chmodSync(file, 0o600);
    }
  }
}
import { Bus, MAGI_CONTACT_ID, SHELL_MAGI_HANDLE, SHELL_MAGI_NICKNAME } from "@magi/bus";
import { ManagerWorker, healthyOrExit, untilHealthy } from "@magi/manager";
import { AspProcessWorker, WebappWorker } from "@magi/webapp";
import { shellWorkers } from "./roles.js";

type ShellContext = ConstructorParameters<typeof WebappWorker>[1];

export class Shell {
  readonly bus: Bus;
  readonly worker: WebappWorker;
  readonly aspProcess: AspProcessWorker;
  private readonly manager: ManagerWorker;
  private ready: Promise<void> | null = null;
  private stopping: Promise<void> | null = null;

  constructor(context: ShellContext) {
    const workspace = prepareShellWorkspace(context.paths.home);
    secureShellDatabases(workspace);
    this.bus = new Bus(SHELL_MAGI_HANDLE, workspace);
    this.bus.contacts.update(MAGI_CONTACT_ID, { nickname: SHELL_MAGI_NICKNAME });
    try {
      secureShellDatabases(workspace);
      this.aspProcess = new AspProcessWorker(this.bus, {
        sourceCheckout: context.paths.checkout,
        node: context.tools.node,
        env: context.tools.env,
        spawnProcess: context.spawn,
        startProcess: context.startProcess,
        initialProcess: context.initialProcesses?.find(({ spec }: { spec: ProcessSpec }) => spec.entry.endsWith("asp.js")),
      });
      this.worker = new WebappWorker(this.bus, { ...context, aspProcess: this.aspProcess });
      this.manager = new ManagerWorker(this.bus, shellWorkers(this.bus, this.worker, this.aspProcess), {
        drain: ["agent", "webapp"],
        build: { app: "shell", script: "build:process" },
      });
    } catch (error) {
      this.bus.close();
      throw error;
    }
  }

  start(): Promise<void> {
    this.ready ??= (async () => {
      await this.manager.start();
      await this.worker.methods.ready();
      await healthyOrExit(() => this.healthy());
    })();
    return this.ready;
  }

  /** Null when the workers that should be up have nothing wrong. */
  healthy(): string | null { return this.manager.healthy(); }

  get stopped(): boolean { return this.stopping !== null; }

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
  const shell = new Shell(context);
  const ready = shell.start();
  let startError: unknown;
  void ready.catch((error: unknown) => {
    startError = error;
  });
  const methods = shell.worker.methods;
  const whenReady = async <T>(run: () => T | Promise<T>): Promise<T> => {
    try {
      await ready;
    } catch (error) {
      throw startError ?? error;
    }
    if (shell.stopped) throw new Error("The Shell backend is disposed.");
    return await run();
  };
  return {
    ...methods,
    ready: () => whenReady(() => undefined),
    prepare: (progress: Parameters<typeof methods.prepare>[0]) =>
      whenReady(() => methods.prepare(progress)),
    start: (progress: Parameters<typeof methods.start>[0]) =>
      whenReady(() => methods.start(progress)),
    dispose: () => shell.stop(),
    shutdown: () => shell.stop(true),
  };
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (value === undefined || value === "") throw new Error(`${name} is not set`);
  return value;
}

export async function runShell(): Promise<(() => Promise<void>) | void> {
  if (process.env.MAGI_HEALTH === "1") {
    const home = process.env.MAGI_HEALTH_WORKSPACE ?? "";
    const shell = new Shell({
      paths: { checkout: home, appCheckout: home, home, userData: `${home}/electron` },
      repository: "https://github.com/realTaki/MAGI.git",
      managed: false,
      tools: { git: "git", node: process.execPath, npm: "npm", env: process.env },
      emit: () => {},
      openExternal: async () => {},
      copy: () => {},
    });
    await shell.start();
    const problem = await untilHealthy(() => shell.healthy());
    await shell.stop();
    if (problem) {
      console.error(problem);
      process.exitCode = 1;
    }
    return;
  }
  const { connectHost } = await import("./host.js");
  const host = connectHost(process.stdin, process.stdout);
  let retire: (() => Promise<void>) | undefined;
  try {
    const checkout = requireEnv("MAGI_CHECKOUT");
    const initialProcesses = await host.restoreProcesses();
    const api = createLocalApi({
      initialProcesses,
      paths: {
        checkout,
        appCheckout: process.env.MAGI_APP_CHECKOUT || checkout,
        home: process.env.MAGI_OS_HOME?.trim() || dirname(magiHome()),
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
      command: (file: string, args: string[], options: { cwd: string; env?: NodeJS.ProcessEnv; description?: string }) => host.command({ file, args, options }),
      prepareDependencies: process.env.MAGI_MANAGED === "1" ? (root: string) => host.prepareDependencies(root) : undefined,
    });
    retire = () => api.dispose();
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
    await api.ready();
    return async () => {
      try { await retire?.(); }
      finally { host.close(); }
    };
  } catch (error) {
    try { await retire?.(); }
    finally { host.close(); }
    throw error;
  }
}

