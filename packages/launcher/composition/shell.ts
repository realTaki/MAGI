import { codexOAuthSessions } from "./oauth.js";
/**
 * Shell EVA workers, including the UI backend. The eva launcher starts this
 * inside the child process. Electron is the window; this process is the BUS.
 */

import { existsSync, chmodSync } from "node:fs";
import { dirname, join } from "node:path";
import { magiHome } from "@magi/launcher/runtime/home.js";


export function operatingSystemHome(): string {
  const explicit = process.env.MAGI_OS_HOME?.trim();
  if (explicit) return explicit;
  if (process.env.MAGI_HOME?.trim()) throw new Error("MAGI_OS_HOME is not set");
  return dirname(magiHome());
}

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
import { FeedWorker } from "@magi/feed";
import { Bus, SHELL_MAGI_HANDLE } from "@magi/bus";
import { LauncherWorker, healthyOrExit } from "@magi/launcher";
import { WebappWorker } from "@magi/webapp";
import { PeerWorker, discoverPeer } from "@magi/channel-a2a/worker.js";
import { shellWorkers, HOSTED_NODE_WORKERS } from "./roles.js";

type ShellContext = ConstructorParameters<typeof WebappWorker>[1];

export class Shell {
  readonly bus: Bus;
  readonly worker: WebappWorker;
  readonly peer: PeerWorker;
  private readonly manager: LauncherWorker;
  private ready: Promise<void> | null = null;
  private stopping: Promise<void> | null = null;
  private stopRequested = false;

  constructor(context: ShellContext) {
    const workspace = prepareShellWorkspace(context.paths.home);
    secureShellDatabases(workspace);
    this.bus = new Bus(SHELL_MAGI_HANDLE, workspace);
    try {
      secureShellDatabases(workspace);
      this.peer = new PeerWorker(this.bus);
      this.worker = new WebappWorker(this.bus, { ...context,
        peerTransport: { discover: discoverPeer },
        nodeControl: { running: () => this.manager.nodeRunning(), setRunning: (enabled: boolean) => this.manager.setNodeRunning(enabled) },
      });
      this.manager = new LauncherWorker(this.bus, shellWorkers(this.bus, this.worker, this.peer, context.client, new FeedWorker(this.bus)), {
        oauthSessions: codexOAuthSessions(),
        drain: ["agent", "webapp"],
        nodeWorkers: HOSTED_NODE_WORKERS,
        build: { app: "shell", script: "build:process" },
        installDesktopUpdate: context.installDesktopUpdate,
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

  get stopped(): boolean { return this.stopRequested; }

  async stop(shutdown = false): Promise<void> {
    this.stopRequested = true;
    if (shutdown) {
      this.worker.requestShutdown();

    }
    if (this.stopping) {
      await this.stopping;
      if (shutdown) {
        await this.worker.stop();

      }
      return;
    }
    const stopping = this.stopOnce();
    this.stopping = stopping;
    try { await stopping; }
    catch (error) {
      // A live mutation can refuse graceful shutdown. BUS is still open; allow
      // the owner to settle and a subsequent stop to finish instead of caching
      // the rejected promise forever.
      if (this.stopping === stopping) this.stopping = null;
      throw error;
    }
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

