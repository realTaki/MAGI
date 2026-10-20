/**
 * The manager worker: start, stop, and restart the other workers on a BUS.
 *
 * The process entry still chooses which workers exist and in which order.
 * This worker runs them and answers `ManageWorkerNotify`. Card, config, and
 * self-replace are their own workers, supervised here.
 */

import { BaseWorker, messageDelivery, waitForJob, type Bus, type ManageWorkerNotify } from "@magi/bus";
import { ConfigWorker } from "./config.js";
import { ReplaceWorker, type AppBuild, type ReplaceHost } from "./replace.js";

const POLL_MS = 20;
const HEALTH_MS = 1_000;
/** Live replace restarts the peer server after the previous child exits. The
 * launcher waits 30s for `ready`; an 8s budget makes that look like a crash. */
const REPLACE_HEALTH_MS = 30_000;
/** A poll loop that throws this many times in a row is worth telling the operator about. */
const FAILURES_BEFORE_NOTICE = 3;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The replacement process must not tell the shell it is ready until this
 * returns. An unhealthy replacement exits so the shell can start the old one.
 * Recording a failed replace does not exit.
 */
export async function healthyOrExit(read: () => string | null): Promise<void> {
  if (!process.env.MAGI_REPLACE_JOB || process.env.MAGI_REPLACE_ERROR) return;
  const problem = await untilHealthy(read, REPLACE_HEALTH_MS);
  if (problem) {
    console.error(problem);
    process.exit(1);
  }
}

/** Wait until every wanted worker is up, or return what is still wrong. */
export async function untilHealthy(read: () => string | null, timeoutMs = 8_000): Promise<string | null> {
  const deadline = Date.now() + timeoutMs;
  let problem: string | null = "workers did not become healthy";
  while (Date.now() < deadline) {
    problem = read();
    if (problem === null) return null;
    await sleep(50);
  }
  return problem;
}

/** A worker the manager can run: `poll()` is the contract, the rest is optional. */
export type StartableWorker = {
  readonly worker_name: string;
  poll(): Promise<boolean>;
  start?(): void | Promise<void>;
  stop?(): void | Promise<void>;
  /** Cancel active agent work before an intentional node pause. */
  interrupt?(): void | Promise<void>;
  /** True after owned work settles, allowing a previously failed stop to retry. */
  canFinishStop?(): boolean;
  /** False while this worker has nothing to work with, such as a channel token. */
  configured?(): boolean;
  /** What is wrong right now, or null when nothing is. Asked between polls. */
  health?(): string | null;
};

type RunningWorker = { worker: StartableWorker; loop: Promise<void>; stopping?: Promise<void> };

export type ManagerOptions = {
  /** Workers that must finish their current poll before anything else stops. */
  drain?: string[];
  /** Hosted node workers, excluding the UI backend and relay infrastructure. */
  nodeWorkers?: string[];
  /** Which app this process builds and replaces. */
  build: AppBuild;
  installDesktopUpdate?: ReplaceHost["installDesktopUpdate"];
};

export class ManagerWorker extends BaseWorker {
  readonly worker_name = "manager";
  private readonly workers: StartableWorker[];
  readonly installDesktopUpdate?: ReplaceHost["installDesktopUpdate"];
  private readonly drain: string[];
  private readonly nodeWorkers: Set<string>;
  private readonly running = new Map<string, RunningWorker>();
  /** Failed stops still own work and must settle before restart or BUS closure. */
  private readonly stopping = new Map<string, RunningWorker>();
  /** What was last reported about each running worker, so only changes notify. */
  private readonly health = new Map<string, string | null>();
  private readonly failures = new Map<string, number>();
  private readonly failing = new Set<string>();
  private started = false;
  private initializing = false;
  private loop: Promise<void> | null = null;
  private lastHealthCheck = 0;
  private lifecycleGeneration = 0;
  /** A failed replacement resume, never an instruction to undo deliberate stop. */
  private resumePending: number | null = null;
  private resumeTask: Promise<void> | null = null;
  private stopRequested = false;

  constructor(bus: Bus, workers: StartableWorker[], options: ManagerOptions) {
    super(bus);
    this.installDesktopUpdate = options.installDesktopUpdate;
    this.drain = options.drain ?? [];
    this.nodeWorkers = new Set(options.nodeWorkers ?? []);
    this.workers = [
      ...workers,
      new ConfigWorker(bus),
      new ReplaceWorker(bus, options.build, this),
    ];
  }

  get up(): boolean { return this.started; }

  isRunning(name: string): boolean { return this.running.has(name); }

  nodeRunning(): boolean {
    return this.started && this.bus.settings.get("node.enabled") !== "false" && this.running.has("agent") &&
      [...this.nodeWorkers].every((name) => {
        const worker = this.workers.find((item) => item.worker_name === name);
        return worker !== undefined && (!this.wanted(worker) || this.running.has(name));
      });
  }

  async setNodeRunning(enabled: boolean): Promise<{ running: boolean }> {
    if (!this.started || !this.nodeWorkers.size) throw new Error("Hosted node control is unavailable");
    const board = this.bus.board("ManageWorkerNotify");
    const id = board.publish({ worker: "magi", action: enabled ? "start" : "stop" }, "operator");
    const result = await waitForJob(() => board.result(id), 30_000, "Node lifecycle");
    if (result.status === "failed") throw new Error(result.error ?? "Node lifecycle failed");
    if (enabled && !this.nodeRunning()) throw new Error(this.healthy() ?? "Hosted node did not start");
    return { running: this.nodeRunning() };
  }

  /** Null when every worker that should be running is up and reports nothing wrong. */
  healthy(): string | null {
    if (this.stopping.size) return `${[...this.stopping.keys()].join(", ")} still stopping; unfinished work retains ownership`;
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
    for (const name of this.stopping.keys()) await this.stopWorker(name);
    if (this.started) return;
    this.stopRequested = false;
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
    this.stopRequested = true;
    this.lifecycleGeneration++;
    this.resumePending = null;
    await this.interruptWorkers();
    for (const name of this.drain) await this.stopWorker(name);
    this.started = false;
    await this.loop;
    this.loop = null;
    await this.resumeTask?.catch(() => undefined);
    await this.stopWorkers(new Set([...this.running.keys(), ...this.stopping.keys()]));
  }

  /**
   * Cancel in-flight turns and tools before draining, then stop everyone except
   * replace. A long-running tool must not force the launcher to kill the worker.
   */
  async suspendForReplace(): Promise<void> {
    this.lifecycleGeneration++;
    this.resumePending = null;
    await this.interruptWorkers();
    for (const name of this.drain) await this.stopWorker(name);
    await this.resumeTask?.catch(() => undefined);
    await this.stopWorkers([...new Set([...this.running.keys(), ...this.stopping.keys()])].filter((name) => name !== "replace"));
  }

  private async stopWorkers(names: Iterable<string>): Promise<void> {
    const errors: unknown[] = [];
    for (const name of names) {
      try { await this.stopWorker(name); } catch (error) { errors.push(error); }
    }
    if (errors.length) throw new Error(errors.map(message).join("; "));
  }

  private async interruptWorkers(): Promise<void> {
    await Promise.all([...this.running.values()].map(({ worker }) => worker.interrupt?.()));
  }

  /** Bring back the workers a failed or finished replace stopped. */
  async resume(): Promise<void> {
    if (!this.started || this.stopRequested) return;
    if (this.resumeTask) return this.resumeTask;
    const generation = this.lifecycleGeneration;
    this.resumePending = generation;
    this.resumeTask = this.reconcile(generation);
    try {
      await this.resumeTask;
      if (this.currentResume(generation)) this.resumePending = null;
    } catch (error) {
      if (this.currentResume(generation) && !this.stopping.size) this.resumePending = null;
      throw error;
    } finally { this.resumeTask = null; }
  }

  private currentResume(generation: number): boolean {
    return this.started && !this.stopRequested && this.lifecycleGeneration === generation && this.resumePending === generation;
  }

  private async recoverResume(): Promise<boolean> {
    const generation = this.resumePending;
    if (generation === null || this.resumeTask || !this.currentResume(generation)) return false;
    // No repeated grace-period waits: retry only once every retained owner has
    // genuinely settled. Owners without this signal still require manual repair.
    if ([...this.stopping.values()].some(({ worker }) => worker.canFinishStop?.() !== true)) return false;
    await this.resume();
    return true;
  }

  async poll(): Promise<boolean> {
    this.checkHealth();
    const board = this.bus.board("ManageWorkerNotify");
    const job = board.claim(this.worker_name);
    if (!job) return this.recoverResume();
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
      catch (error) { this.recordFailure(this.worker_name, message(error)); }
      if (!worked && this.started) await sleep(POLL_MS);
    }
  }

  /** Start, stop, or restart one worker by name. The settings stay the source of truth. */
  private async apply(input: ManageWorkerNotify): Promise<void> {
    if (input.worker === "magi" && this.nodeWorkers.size) {
      this.bus.settings.set("node.enabled", input.action === "stop" ? "false" : "true");
      if (input.action === "stop") {
        await Promise.all([...this.nodeWorkers].map((name) => this.workers.find((worker) => worker.worker_name === name)?.interrupt?.()));
      }
      await this.reconcile();
      return;
    }
    const worker = this.workers.find((candidate) => candidate.worker_name === input.worker);
    if (!worker) throw new Error(`unknown worker ${input.worker}`);
    this.bus.settings.set(disabledKey(worker.worker_name), input.action === "stop" ? "false" : "true");
    // A restart builds no new object: a worker reads what it needs when it starts, so
    // stopping and starting it again is what picks up a changed token.
    if (input.action === "restart") await this.stopWorker(worker.worker_name);
    await this.reconcile();
  }

  /** Converge: run what is configured, stop what is not. */
  private async reconcile(resumeGeneration?: number): Promise<void> {
    // No new worker generation while any old generation still owns mutations.
    for (const name of this.stopping.keys()) {
      if (resumeGeneration !== undefined && !this.currentResume(resumeGeneration)) return;
      await this.stopWorker(name);
    }
    for (const worker of this.workers) {
      if (resumeGeneration !== undefined && !this.currentResume(resumeGeneration)) return;
      const name = worker.worker_name;
      const running = this.running.has(name);
      const wanted = this.wanted(worker);
      if (wanted === running) continue;
      if (wanted) await this.startWorker(worker, resumeGeneration);
      else await this.stopWorker(name);
    }
  }

  private wanted(worker: StartableWorker): boolean {
    // "Stopped" is a decision, not a one-off: a worker that came back on the next
    // reconcilation would make stopping it useless.
    if (this.nodeWorkers.has(worker.worker_name) && this.bus.settings.get("node.enabled") === "false") return false;
    if (this.bus.settings.get(disabledKey(worker.worker_name)) === "false") return false;
    return worker.configured?.() ?? true;
  }

  private async startWorker(worker: StartableWorker, resumeGeneration?: number): Promise<void> {
    const name = worker.worker_name;
    try {
      await worker.start?.();
    } catch (error) {
      this.report(`worker "${name}" could not start: ${message(error)}`);
      try { await worker.stop?.(); } catch { /* a failed start has nothing left to stop */ }
      return;
    }
    if (resumeGeneration !== undefined && !this.currentResume(resumeGeneration)) {
      // stop() may arrive while start() is awaiting. Retire that cold instance
      // without giving it a poll loop or accepting new work.
      if (worker.stop) this.stopping.set(name, { worker, loop: Promise.resolve() });
      await worker.interrupt?.();
      if (worker.stop) await this.stopWorker(name);
      return;
    }
    const running: RunningWorker = { worker, loop: Promise.resolve() };
    this.running.set(name, running);
    this.health.set(name, worker.health?.() ?? null);
    this.failures.delete(name);
    if (!this.initializing) running.loop = this.runLoop(name, worker);
  }

  private async stopWorker(name: string): Promise<void> {
    const running = this.running.get(name) ?? this.stopping.get(name);
    if (!running) return;
    if (running.stopping) return running.stopping;
    // Dropping it first keeps the loop from starting one more poll while it stops.
    this.running.delete(name);
    this.health.delete(name);
    this.failures.delete(name);
    this.failing.delete(name);
    // Preserve legacy poll-generation detachment for workers without a stop
    // hook. Retained shutdown ownership applies to explicit lifecycle owners.
    if (!running.worker.stop) { await running.loop; return; }
    this.stopping.set(name, running);
    running.stopping = (async () => {
      await running.loop;
      await running.worker.stop?.();
      this.stopping.delete(name);
    })();
    try { await running.stopping; }
    finally { running.stopping = undefined; }
  }

  private runLoop(name: string, worker: StartableWorker): Promise<void> {
    const generation = this.running.get(name);
    return (async () => {
      while (this.started && this.running.get(name) === generation && !(this.nodeWorkers.has(name) && this.bus.settings.get("node.enabled") === "false")) {
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
    const text = `worker "${name}" failed: ${message(error)}`;
    if (failures === 1) this.recordFailure(name, text);
    if (failures < FAILURES_BEFORE_NOTICE || this.failing.has(name)) return;
    this.failing.add(name);
    this.report(`worker "${name}" keeps failing: ${message(error)}`);
  }

  /** A thrown poll is a failed job, not a log line. */
  private recordFailure(worker: string, text: string): void {
    this.bus.board("ManageWorkerNotify").seal(this.worker_name, { worker, action: "restart" }, text);
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
    if (text.endsWith("recovered")) { if (messageDelivery.notify(this.bus, notice) === null) console.error(notice); }
    else messageDelivery.reportError(this.bus, notice, { module: this.worker_name });
  }
}

/** Stopping is durable: the supervisor keeps that decision in the workspace settings. */
function disabledKey(worker: string): string {
  return `worker.${worker}.enabled`;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
