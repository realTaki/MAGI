/**
 * The supervisor: the one worker that decides which other workers run.
 *
 * The entry point used to start a fixed list at boot, so anything not configured at
 * that moment — a channel whose credentials arrive later — could only come up after a
 * restart. Now the entry hands this worker the list and nothing else: the manager asks
 * each one whether it is configured, starts what is, and reacts to `ManageWorkerNotify`.
 */

import { BaseWorker, HOME_CONVERSATION_ID, type Bus, type ManageWorkerNotify } from "../bus/index.js";

/** A worker the manager can run: `poll()` is the contract, the rest is optional. */
export type StartableWorker = BaseWorker & {
  start?(): void | Promise<void>;
  stop?(): void | Promise<void>;
  /** False while this worker has nothing to work with, such as a channel token. */
  configured?(): boolean;
  /** What is wrong right now, or null when nothing is. Asked between polls. */
  health?(): string | null;
};

const POLL_MS = 20;
const HEALTH_MS = 1_000;
/** A poll loop that throws this many times in a row is worth telling the operator about. */
const FAILURES_BEFORE_NOTICE = 3;

type Running = { worker: StartableWorker; loop: Promise<void> };

export class ManagerWorker extends BaseWorker {
  readonly worker_name = "manager";
  private readonly running = new Map<string, Running>();
  /** What was last reported about each running worker, so only changes notify. */
  private readonly health = new Map<string, string | null>();
  private readonly failures = new Map<string, number>();
  private readonly failing = new Set<string>();
  private supervising = false;
  private lastHealthCheck = 0;

  constructor(bus: Bus, private readonly workers: StartableWorker[]) {
    super(bus);
  }

  /** Boot: bring up every worker that is configured. Failures are reported, not thrown. */
  async start(): Promise<void> {
    this.supervising = true;
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

  async stop(): Promise<void> {
    this.supervising = false;
    for (const name of [...this.running.keys()]) await this.stopWorker(name);
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
    const running: Running = { worker, loop: Promise.resolve() };
    this.running.set(name, running);
    this.health.set(name, worker.health?.() ?? null);
    this.failures.delete(name);
    running.loop = this.runLoop(name, worker);
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
      while (this.supervising && this.running.has(name)) {
        let worked = false;
        try {
          worked = await worker.poll();
          this.recovered(name);
        } catch (error) {
          this.fail(name, error);
        }
        if (!worked && this.supervising) await sleep(POLL_MS);
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
   * Tell the operator, in their own conversation — number 0, the thread their first
   * message created. Before they have ever spoken there is nowhere to put it, so it
   * stays in the log.
   */
  private report(text: string): void {
    const home = this.bus.conversations.get(HOME_CONVERSATION_ID);
    if (home === null) {
      console.error(`[manager] ${text}`);
      return;
    }
    this.bus.publishDelivery({ conversation_id: home.id, text: `[manager] ${text}` });
  }
}

/** Stopping is durable: the manager keeps that decision in the workspace settings. */
function disabledKey(worker: string): string {
  return `worker.${worker}.enabled`;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
