/**
 * One ASP process: its BUS, and the supervision of the workers on it.
 *
 * Same shape as `apps/eva/eva.ts`. This file is the supervisor. The relay itself
 * is a worker (`AspServer`) that later moves to `packages/`.
 */

import { dirname } from "node:path";
import { Bus, messageDelivery, type ManageWorkerNotify } from "@magi/bus";

import { defaultDatabasePath } from "./db/database.ts";
import { AspServer, intranetBaseUrl, type CreateAppOptions } from "./server/app.ts";
import { ASP_HANDLE } from "./server/relayWorker.ts";

const POLL_MS = 20;
const FAILURES_BEFORE_NOTICE = 3;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A worker the entry can run: `poll()` is the contract, the rest is optional. */
export type StartableWorker = {
  readonly worker_name: string;
  poll(): Promise<boolean>;
  start?(): void | Promise<void>;
  stop?(): void | Promise<void>;
  configured?(): boolean;
  health?(): string | null;
};

type RunningWorker = { worker: StartableWorker; loop: Promise<void> };

export class Asp {
  readonly worker_name = "manager";
  readonly bus: Bus;
  readonly server: AspServer;
  private readonly workers: StartableWorker[];
  private readonly running = new Map<string, RunningWorker>();
  private readonly failures = new Map<string, number>();
  private readonly failing = new Set<string>();
  private up = false;
  private loop: Promise<void> | null = null;

  constructor(options: CreateAppOptions = {}) {
    const databasePath = options.databasePath ?? defaultDatabasePath();
    this.bus = new Bus(ASP_HANDLE, options.workspace ?? dirname(databasePath));
    this.server = new AspServer(this.bus, { ...options, databasePath });
    this.workers = [this.server];
  }

  get origin(): string { return this.server.origin; }
  get database() { return this.server.database; }
  get store() { return this.server.store; }
  get transport() { return this.server.transport; }
  get operatorHandle() { return this.server.operatorHandle; }
  get operatorToken() { return this.server.operatorToken; }

  async start(): Promise<void> {
    if (this.up) return;
    this.up = true;
    this.loop = this.runSupervisor();
    await this.reconcile();
    if (this.running.has(this.server.worker_name)) return;
    this.up = false;
    await this.loop;
    this.loop = null;
    throw new Error(`worker "${this.server.worker_name}" could not start`);
  }

  async stop(): Promise<void> {
    this.up = false;
    await this.loop;
    this.loop = null;
    for (const name of [...this.running.keys()]) await this.stopWorker(name);
    this.bus.close();
  }

  private async runSupervisor(): Promise<void> {
    while (this.up) {
      let worked = false;
      try { worked = await this.poll(); }
      catch (error) { console.error("manager:", error); }
      if (!worked && this.up) await sleep(POLL_MS);
    }
  }

  private async poll(): Promise<boolean> {
    const board = this.bus.board("ManageWorkerNotify");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    try {
      await this.apply(job.input);
      board.submit(this.worker_name, job.id, { output: { running: [...this.running.keys()] } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: message(error) });
    }
    return true;
  }

  private async apply(input: ManageWorkerNotify): Promise<void> {
    const worker = this.workers.find((candidate) => candidate.worker_name === input.worker);
    if (!worker) throw new Error(`unknown worker ${input.worker}`);
    this.bus.settings.set(disabledKey(worker.worker_name), input.action === "stop" ? "false" : "true");
    if (input.action === "restart") await this.stopWorker(worker.worker_name);
    await this.reconcile();
  }

  private async reconcile(): Promise<void> {
    for (const worker of this.workers) {
      const name = worker.worker_name;
      const running = this.running.has(name);
      const wanted = this.wanted(worker);
      if (wanted === running) continue;
      if (wanted) await this.startWorker(worker);
      else await this.stopWorker(name);
    }
  }

  private wanted(worker: StartableWorker): boolean {
    if (this.bus.settings.get(disabledKey(worker.worker_name)) === "false") return false;
    return worker.configured?.() ?? true;
  }

  private async startWorker(worker: StartableWorker): Promise<void> {
    const name = worker.worker_name;
    try {
      await worker.start?.();
    } catch (error) {
      this.report(`worker "${name}" could not start: ${message(error)}`);
      try { await worker.stop?.(); } catch { /* a failed start has nothing left to stop */ }
      return;
    }
    const running: RunningWorker = { worker, loop: Promise.resolve() };
    this.running.set(name, running);
    this.failures.delete(name);
    running.loop = this.runLoop(name, worker);
  }

  private async stopWorker(name: string): Promise<void> {
    const running = this.running.get(name);
    if (!running) return;
    this.running.delete(name);
    this.failures.delete(name);
    this.failing.delete(name);
    await running.loop;
    await running.worker.stop?.();
  }

  private runLoop(name: string, worker: StartableWorker): Promise<void> {
    return (async () => {
      while (this.up && this.running.has(name)) {
        let worked = false;
        try {
          worked = await worker.poll();
          this.recovered(name);
        } catch (error) {
          this.fail(name, error);
        }
        if (!worked && this.up) await sleep(POLL_MS);
      }
    })();
  }

  private fail(name: string, error: unknown): void {
    const failures = (this.failures.get(name) ?? 0) + 1;
    this.failures.set(name, failures);
    console.error(`${name}:`, error);
    if (failures < FAILURES_BEFORE_NOTICE || this.failing.has(name)) return;
    this.failing.add(name);
    this.report(`worker "${name}" keeps failing: ${message(error)}`);
  }

  private recovered(name: string): void {
    if (!this.failures.delete(name)) return;
    if (this.failing.delete(name)) this.report(`worker "${name}" recovered`);
  }

  private report(text: string): void {
    const notice = `[manager] ${text}`;
    if (messageDelivery.notify(this.bus, notice) === null) console.error(notice);
  }
}

function disabledKey(worker: string): string {
  return `worker.${worker}.enabled`;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export type { CreateAppOptions };
export { intranetBaseUrl };

export function createApp(options: CreateAppOptions = {}): Asp {
  return new Asp(options);
}
