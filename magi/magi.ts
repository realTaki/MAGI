/**
 * One long-lived MAGI: its BUS, and the supervision of the workers on it.
 *
 * This file is the only one allowed to know every module, so it owns the two things no
 * worker can own for itself: which workers exist, and when each of them runs. That is
 * why the entry *is* the supervisor rather than starting a fixed list at boot — a
 * channel whose credentials arrive later comes up when they do, and the entry answers
 * `ManageWorkerNotify` like any other worker would.
 */

import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { Bus, type ManageWorkerNotify } from "./bus/index.js";
import { AgentWorker } from "./agent/worker.js";
import { ProvidersWorker } from "./providers/worker.js";
import type { LLMClient } from "./providers/client.js";
import { ToolsWorker } from "./tools/worker.js";
import type { Tool } from "./tools/registry.js";
import { CliWorker } from "./channels/cli/worker.js";
import { AspWorker } from "./channels/asp/worker.js";
import { TelegramWorker } from "./channels/telegram/worker.js";
import { TaskWorker } from "./channels/tasks/worker.js";
import { McpWorker, type McpConnector } from "./mcp/worker.js";

const POLL_MS = 20;
const HEALTH_MS = 1_000;
/** A poll loop that throws this many times in a row is worth telling the operator about. */
const FAILURES_BEFORE_NOTICE = 3;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A worker the entry can run: `poll()` is the contract, the rest is optional. */
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

export type MagiOptions = {
  workspace?: string;
  client?: LLMClient;
  tools?: Tool[];
  deliver?: (text: string) => void;
  asp?: { base: string; token: string };
  telegram?: { token: string; apiBase?: string };
  mcpConnector?: McpConnector;
};

export class Magi {
  readonly bus: Bus;
  /** The name this entry claims `ManageWorkerNotify` with. */
  readonly worker_name = "manager";
  private readonly workers: StartableWorker[];
  private readonly running = new Map<string, RunningWorker>();
  /** What was last reported about each running worker, so only changes notify. */
  private readonly health = new Map<string, string | null>();
  private readonly failures = new Map<string, number>();
  private readonly failing = new Set<string>();
  private up = false;
  private loop: Promise<void> | null = null;
  private lastHealthCheck = 0;

  constructor(handle: string, options: MagiOptions = {}) {
    this.bus = new Bus(handle, options.workspace);
    this.workers = [
      new AgentWorker(this.bus),
      new ToolsWorker(this.bus, options.tools),
      new ProvidersWorker(this.bus, options.client),
      new CliWorker(this.bus, options.deliver),
      new TaskWorker(this.bus),
      new McpWorker(this.bus, options.mcpConnector),
      ...(options.asp ? [new AspWorker(this.bus, options.asp.base, options.asp.token)] : []),
      new TelegramWorker(this.bus, options.telegram),
    ];
  }

  /** Bring up every worker that is configured. Failures are reported, not thrown. */
  async start(): Promise<void> {
    if (this.up) return;
    this.up = true;
    this.loop = this.runSupervisor();
    await this.reconcile();
  }

  async stop(): Promise<void> {
    // The agent is the one worker that has to *finish* rather than be stopped: a turn it
    // already accepted may still need a provider to answer and a channel to deliver, so
    // everything else stays up until it has drained. Stopping the loops first would leave
    // those jobs unclaimed and the drain waiting for a timeout.
    const agent = this.running.get("agent");
    if (agent) {
      this.running.delete("agent");
      this.health.delete("agent");
      await agent.loop;
      await agent.worker.stop?.();
    }
    this.up = false;
    await this.loop;
    this.loop = null;
    for (const name of [...this.running.keys()]) await this.stopWorker(name);
    this.bus.close();
  }

  async chat(text: string, address = "terminal"): Promise<number> {
    if (!this.up) throw new Error("MAGI is not running");
    const conversation = this.bus.conversations.forChannel("cli", address);
    // The terminal is the operator of a hand-run MAGI, and their first message is what
    // establishes where this workspace reports trouble.
    if (this.bus.homeConversation() === null) this.bus.setHomeConversation(conversation.id);
    const id = this.bus.publishChat({ conversation_id: conversation.id, text });
    while (this.up) {
      if (this.bus.board("ChatNotify").result(id)) return id;
      await sleep(20);
    }
    throw new Error("MAGI stopped before completing the turn");
  }

  /** The supervisor's own loop, which is all the entry runs itself. */
  private async runSupervisor(): Promise<void> {
    while (this.up) {
      let worked = false;
      try { worked = await this.poll(); }
      catch (error) { console.error("manager:", error); }
      if (!worked && this.up) await sleep(POLL_MS);
    }
  }

  private async poll(): Promise<boolean> {
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
   * Tell the operator. `publishNotice` owns where that is, and answers null when they
   * have never spoken: then, and only then, a log line is the best there is.
   */
  private report(text: string): void {
    const notice = `[manager] ${text}`;
    if (this.bus.publishNotice(notice) === null) console.error(notice);
  }
}

/** Stopping is durable: the supervisor keeps that decision in the workspace settings. */
function disabledKey(worker: string): string {
  return `worker.${worker}.enabled`;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function main(): Promise<void> {
  const handle = process.argv[2];
  if (!handle) throw new Error("usage: bun run start -- @handle.magi [asp-base asp-token]");
  const [base, token] = process.argv.slice(3);
  if ((base && !token) || (!base && token)) throw new Error("ASP base and token must be supplied together");
  const magi = new Magi(handle, { asp: base && token ? { base, token } : undefined });
  await magi.start();
  // With ASP the process is a service: it stays up until it is asked to stop.
  // Without one it is a prompt, which is how a MAGI is tried out by hand.
  if (base && token) {
    try {
      await new Promise<void>((resolve) => {
        process.once("SIGINT", resolve);
        process.once("SIGTERM", resolve);
      });
    } finally { await magi.stop(); }
    return;
  }
  const input = createInterface({ input: stdin, output: stdout });
  try {
    while (true) {
      const line = await input.question("> ");
      if (line.trim() === "/exit") break;
      if (line.trim()) await magi.chat(line);
    }
  } finally { input.close(); await magi.stop(); }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
