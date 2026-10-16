import { realpathSync } from "node:fs";
/**
 * One operator process: its BUS, and the workers on it.
 *
 * Same shape as `apps/eva/eva.ts`. This file chooses the workers.
 * ManagerWorker runs them. The backend is `@magi/webapp`.
 *
 * The BUS is owned by the host MAGI (`@eva-000.magi`, Balthasar). The webapp
 * still speaks as `@user.magi`. Channels that attach that MAGI to ASP are not
 * on this list yet.
 */

import { fileURLToPath } from "node:url";
import { prepareShellWorkspace, secureShellDatabases } from "./workspace.ts";
import type { ProcessSpec } from "./host.js";
import { Bus, SHELL_MAGI_HANDLE } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";
import { AgentWorker } from "@magi/agent/worker.js";
import { ToolsWorker } from "@magi/built-in-tools/worker.js";
import { ContactsWorker } from "@magi/contacts/worker.js";
import { MemoryWorker } from "@magi/memory/worker.js";
import { ProvidersWorker } from "@magi/providers/worker.js";
import { SkillWorker } from "@magi/skills/worker.js";
import { AspProcessWorker, WebappWorker } from "@magi/webapp";

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
      this.manager = new ManagerWorker(this.bus, [
        // Contacts first: Identity leads the system context, same order as Magi.
        new ContactsWorker(this.bus),
        new SkillWorker(this.bus),
        new AgentWorker(this.bus),
        new ToolsWorker(this.bus),
        new MemoryWorker(this.bus),
        new ProvidersWorker(this.bus),
        this.worker,
        this.aspProcess,
        // TODO: attach host channels (ASP, CLI, tasks, MCP, Telegram).
      ], {
        drain: ["agent", "webapp"],
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
    })();
    return this.ready;
  }

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

async function main(): Promise<void> {
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
    process.send?.({ type: "ready" });
    await new Promise<void>((resolve) => {
      process.once("SIGINT", resolve);
      process.once("SIGTERM", resolve);
    });
  } finally {
    // Close this operator process only; Electron continues to own the other apps.
    try { await retire?.(); }
    finally { host.close(); }
  }
}

if (process.argv[1] && realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1])) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
