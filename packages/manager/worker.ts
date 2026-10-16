/**
 * The manager worker: start, stop, and restart the other workers on a BUS.
 *
 * The process entry still chooses which workers exist and in which order.
 * This worker runs them and answers `ManageWorkerNotify`.
 */

import { execFile, fork, type ChildProcess } from "node:child_process";
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { BaseWorker, decryptWith, MAGI_CONTACT_ID, messageDelivery, providerCard, SYSTEM_CONTACT_ID, type Bus, type ConfigureJob, type ManageWorkerNotify } from "@magi/bus";

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
  /** The startup provider card, if one is waiting for the user. Offered once. */
  private providerCardJob: number | null = null;
  private providerCardChat: number | null = null;
  private providerCardOffered = false;
  private loop: Promise<void> | null = null;
  private lastHealthCheck = 0;

  constructor(bus: Bus, workers: StartableWorker[], options: ManagerOptions = {}) {
    super(bus);
    this.workers = workers;
    this.drain = options.drain ?? [];
  }

  get up(): boolean { return this.started; }

  isRunning(name: string): boolean { return this.running.has(name); }

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

  async poll(): Promise<boolean> {
    this.checkHealth();
    await this.providerSetup();
    if (await this.pollReplace()) return true;
    if (await this.pollConfigure()) return true;
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

  /**
   * No provider key means this node cannot think. Show the provider card from
   * here, not from a model turn. After the user submits it, apply the fields
   * and hand the rest of provider_setup to the agent.
   */
  private async providerSetup(): Promise<void> {
    if (this.providerCardJob !== null) {
      await this.finishProviderCard();
      return;
    }
    if (this.providerCardOffered || this.bus.secrets.get("provider.api_key")) return;
    const chatId = this.cardChat();
    if (chatId === null) return;
    const spec = providerCard(this.bus.handle);
    this.providerCardOffered = true;
    this.providerCardJob = this.bus.board("CardJob").publish({ spec }, "manager");
    this.providerCardChat = chatId;
    messageDelivery.send(this.bus, chatId, `card: ${spec.title}`, MAGI_CONTACT_ID, "manager", { card: spec });
  }

  private cardChat(): number | null {
    const mine = (id: number): boolean =>
      this.bus.chatMembers.list(id).some((member) => member.id === MAGI_CONTACT_ID);
    const home = messageDelivery.homeChat(this.bus);
    const homeChat = home === null ? null : this.bus.chats.get(home);
    if (homeChat?.channel === "asp" && homeChat.delivery_address && mine(homeChat.id)) return homeChat.id;
    const asp = this.bus.chats.list("asp").find((chat) => chat.delivery_address !== "" && mine(chat.id));
    return asp?.id ?? null;
  }

  private async finishProviderCard(): Promise<void> {
    const id = this.providerCardJob;
    const chatId = this.providerCardChat;
    if (id === null || chatId === null) return;
    const result = this.bus.board("CardJob").result(id);
    if (!result) return;
    this.providerCardJob = null;
    this.providerCardChat = null;
    if (result.status === "failed") {
      messageDelivery.notify(this.bus, `[provider] ${result.error ?? "provider card failed"}`, chatId);
      return;
    }
    const fields = result.output?.fields ?? {};
    const settings: Record<string, string> = {};
    if (fields.provider) settings["provider.name"] = fields.provider;
    if (fields.model) settings["provider.model"] = fields.model;
    if (fields.base_url) settings["provider.base_url"] = fields.base_url;
    try {
      await this.configure({ settings, reload: ["providers"] });
    } catch (error) {
      messageDelivery.notify(this.bus, `[provider] ${message(error)}`, chatId);
      return;
    }
    messageDelivery.send(this.bus, chatId, [
      "Provider card submitted.",
      `provider: ${fields.provider ?? ""}`,
      `model: ${fields.model ?? ""}`,
      `base_url: ${fields.base_url ?? ""}`,
      "provider.api_key: updated",
      "Load provider_setup and continue from step 2. Do not show another card.",
    ].join("\n"), SYSTEM_CONTACT_ID, "manager");
  }

  /** Rebuild the checkout. A failed build or health check restores the previous output. */
  private async pollReplace(): Promise<boolean> {
    const board = this.bus.board("ReplaceSelfJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    const checkout = job.input.checkout;
    const live = join(checkout, "apps", "eva", "dist");
    const backup = `${live}.rollback`;
    let replacement: ChildProcess | null = null;
    try {
      snapshot(live, backup);
      await runBuild(checkout);
      const entry = join(live, "eva.js");
      await checkEntry(entry);
      if (job.input.swap) replacement = await spawnReady(entry);
      board.submit(this.worker_name, job.id, { output: { replaced: true } });
      if (replacement) {
        for (const name of this.drain) await this.stopWorker(name);
        replacement.unref();
        process.exit(0);
      }
    } catch (error) {
      replacement?.kill();
      restore(live, backup);
      const text = message(error);
      board.submit(this.worker_name, job.id, { error: text });
      messageDelivery.notify(this.bus, `[replace] ${text}`);
    }
    return true;
  }

  /** Persist settings and secrets. Sealed values are decrypted here and nowhere else. */
  private async pollConfigure(): Promise<boolean> {
    const board = this.bus.board("ConfigureJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    try {
      const written = await this.configure(job.input);
      board.submit(this.worker_name, job.id, { output: { secret_set: written } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: message(error) });
    }
    return true;
  }

  private async configure(input: ConfigureJob): Promise<string[]> {
    const written: string[] = [];
    for (const [key, value] of Object.entries(input.settings ?? {})) {
      this.bus.settings.set(key, value);
    }
    for (const [key, value] of Object.entries(input.secrets ?? {})) {
      this.bus.secrets.set(key, value);
      written.push(key);
    }
    for (const [key, ciphertext] of Object.entries(input.sealed ?? {})) {
      let plain: string;
      try {
        plain = decryptWith(this.bus.identity.privateKey, ciphertext);
      } catch {
        throw new Error(`could not decrypt ${key}`);
      }
      this.bus.secrets.set(key, plain);
      written.push(key);
    }
    for (const name of input.reload ?? []) {
      const worker = this.workers.find((candidate) => candidate.worker_name === name);
      if (!worker) continue;
      await this.stopWorker(name);
      await this.reconcile();
    }
    return written;
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

function snapshot(live: string, backup: string): void {
  rmSync(backup, { recursive: true, force: true });
  if (existsSync(live)) cpSync(live, backup, { recursive: true });
}

function restore(live: string, backup: string): void {
  rmSync(live, { recursive: true, force: true });
  if (existsSync(backup)) cpSync(backup, live, { recursive: true });
}

function runBuild(checkout: string): Promise<void> {
  return exec(process.execPath, ["--run", "build"], checkout);
}

function checkEntry(entry: string): Promise<void> {
  if (!existsSync(entry)) return Promise.reject(new Error(`build produced no ${entry}`));
  return exec(process.execPath, ["--check", entry], dirname(entry));
}

function exec(file: string, args: string[], cwd: string): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { cwd }, (error, _stdout, stderr) => {
      if (error) reject(new Error(stderr.trim() || error.message));
      else resolve();
    });
  });
}

function spawnReady(entry: string): Promise<ChildProcess> {
  const child = fork(entry, [], {
    execPath: process.execPath,
    execArgv: [],
    cwd: dirname(entry),
    stdio: ["ignore", "inherit", "inherit", "ipc"],
  });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("replacement did not become ready"));
    }, 15_000);
    child.once("message", (body: { type?: string }) => {
      if (body?.type !== "ready") return;
      clearTimeout(timer);
      resolve(child);
    });
    child.once("exit", () => {
      clearTimeout(timer);
      reject(new Error("replacement exited before it was ready"));
    });
  });
}
