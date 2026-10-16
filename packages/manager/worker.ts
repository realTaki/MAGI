/**
 * The manager worker: start, stop, and restart the other workers on a BUS.
 *
 * The process entry still chooses which workers exist and in which order.
 * This worker runs them and answers `ManageWorkerNotify`. Card, config, and
 * self-replace are their own workers, supervised here.
 */

import { BaseWorker, messageDelivery, type Bus, type ManageWorkerNotify } from "@magi/bus";
import { ConfigWorker } from "./config.js";
import { ReplaceWorker, type AppBuild } from "./replace.js";

const POLL_MS = 20;
const HEALTH_MS = 1_000;
/** A poll loop that throws this many times in a row is worth telling the operator about. */
const FAILURES_BEFORE_NOTICE = 3;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A worker the manager can run: `poll()` is the contract, the rest is optional. */
export type StartableWorker = {
  readonly worker_name: string;
  poll(): Promise<boolean>;
  start?(): void | Promise<void>;
  stop?(): void | Promise<void>;
  /** False while this worker has nothing to work with, such as a channel token. */
  configured?(): boolean;
  /** What is wrong right now, or null when nothing is. Asked between polls. */
  health?(): string | null;
};

type RunningWorker = { worker: StartableWorker; loop: Promise<void> };

export type ManagerOptions = {
  /** Workers that must finish their current poll before anything else stops. */
  drain?: string[];
  /** Which app this process builds and replaces. */
  build: AppBuild;
};

export class ManagerWorker extends BaseWorker {
  readonly worker_name = "manager";
  private readonly workers: StartableWorker[];
  private readonly drain: string[];
  private readonly running = new Map<string, RunningWorker>();
  /** What was last reported about each running worker, so only changes notify. */
  private readonly health = new Map<string, string | null>();
  private readonly failures = new Map<string, number>();
  private readonly failing = new Set<string>();
  private started = false;
  private initializing = false;
  private loop: Promise<void> | null = null;
  private lastHealthCheck = 0;

  constructor(bus: Bus, workers: StartableWorker[], options: ManagerOptions) {
    super(bus);
    this.drain = options.drain ?? [];
    this.workers = [
      ...workers,
      new ConfigWorker(bus),
      new ReplaceWorker(bus, options.build, this),
    ];
  }

  get up(): boolean { return this.started; }

  isRunning(name: string): boolean { return this.running.has(name); }

  /** Null when every worker that should be running is up and reports nothing wrong. */
  healthy(): string | null {
    for (const worker of this.workers) {
      if (!this.wanted(worker)) continue;
      if (!this.running.has(worker.worker_name)) return `${worker.worker_name} is not running`;
      const problem = worker.health?.() ?? null;
      if (problem) return `${worker.worker_name}: ${problem}`;
    }
    return null;
  }

  /** Bring up every worker that is configured. Failures are reported, not thrown. */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.initializing = true;
    await this.reconcile();
    this.initializing = false;
    // Recovered jobs must see the complete live tool/prompt catalog, including
    // providers and memory, before any worker starts claiming them.
    for (const [name, running] of this.running) running.loop = this.runLoop(name, running.worker);
    this.loop = this.runSupervisor();
  }

  async stop(): Promise<void> {
    for (const name of this.drain) await this.stopWorker(name);
    this.started = false;
    await this.loop;
    this.loop = null;
    for (const name of [...this.running.keys()]) await this.stopWorker(name);
  }

  /**
   * Let in-flight drain workers finish, then stop everyone except replace.
   * The database and the ASP connection are released before a new build starts.
   */
  async suspendForReplace(): Promise<void> {
    for (const name of this.drain) await this.stopWorker(name);
    for (const name of [...this.running.keys()]) {
      if (name === "replace") continue;
      await this.stopWorker(name);
    }
  }

  /** Bring back the workers a failed or finished replace stopped. */
  async resume(): Promise<void> {
    if (!this.started) return;
    await this.reconcile();
  }

  async poll(): Promise<boolean> {
    this.checkHealth();
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

  private async runSupervisor(): Promise<void> {
    while (this.started) {
      let worked = false;
      try { worked = await this.poll(); }
      catch (error) { console.error("manager:", error); }
      if (!worked && this.started) await sleep(POLL_MS);
    }
  }

  /** Start, stop, or restart one worker by name. The settings stay the source of truth. */
  private async apply(input: ManageWorkerNotify): Promise<void> {
    const worker = this.workers.find((candidate) => candidate.worker_name === input.worker);
    if (!worker) throw new Error(`unknown worker ${input.worker}`);
    this.bus.settings.set(disabledKey(worker.worker_name), input.action === "stop" ? "false" : "true");
    // A restart builds no new object: a worker reads what it needs when it starts, so
    // stopping and starting it again is what picks up a changed token.
    if (input.action === "restart") await this.stopWorker(worker.worker_name);
    await this.reconcile();
  }

  /** Converge: run what is configured, stop what is not. */
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
    // "Stopped" is a decision, not a one-off: a worker that came back on the next
    // reconcilation would make stopping it useless.
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
    this.health.set(name, worker.health?.() ?? null);
    this.failures.delete(name);
    if (!this.initializing) running.loop = this.runLoop(name, worker);
  }

  private async stopWorker(name: string): Promise<void> {
    const running = this.running.get(name);
    if (!running) return;
    // Dropping it first keeps the loop from starting one more poll while it stops.
    this.running.delete(name);
    this.health.delete(name);
    this.failures.delete(name);
    this.failing.delete(name);
    await running.loop;
    await running.worker.stop?.();
  }

  private runLoop(name: string, worker: StartableWorker): Promise<void> {
    return (async () => {
      while (this.started && this.running.has(name)) {
        let worked = false;
        try {
          worked = await worker.poll();
          this.recovered(name);
        } catch (error) {
          this.fail(name, error);
        }
        if (!worked && this.started) await sleep(POLL_MS);
      }
    })();
  }

  /** A poll loop that keeps throwing is the one failure a worker cannot report itself. */
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

  private checkHealth(): void {
    const now = Date.now();
    if (now - this.lastHealthCheck < HEALTH_MS) return;
    this.lastHealthCheck = now;
    for (const [name, running] of this.running) {
      const health = running.worker.health?.() ?? null;
      if (this.health.get(name) === health) continue;
      this.health.set(name, health);
      this.report(health === null ? `worker "${name}" recovered` : `worker "${name}": ${health}`);
    }
  }

  /**
   * Tell the operator. `messageDelivery.notify` owns where that is, and answers null when they
   * have never spoken: then, and only then, a log line is the best there is.
   */
  private report(text: string): void {
    const notice = `[manager] ${text}`;
    if (messageDelivery.notify(this.bus, notice) === null) console.error(notice);
  }
}

/** Stopping is durable: the supervisor keeps that decision in the workspace settings. */
function disabledKey(worker: string): string {
  return `worker.${worker}.enabled`;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
