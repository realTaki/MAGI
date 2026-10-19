/**
 * The ASP OS process, as a worker. Spawn, health, and stop live here so the
 * same worker can hang off any handle's manager. Talking to ASP (HTTP, chats,
 * providers) stays on the caller.
 *
 * Manager start/stop is not the same axis as the child living or dying.
 * `start()` spawns only when a checkout is already known: prepare has to
 * create that tree first, so the operator backend calls `ensure` after it.
 * `stop()` kills the child only when the desktop is quitting. A renderer
 * reload stops this worker and must leave the process running. `stopOwned`
 * stops a process this worker started while the worker itself stays up.
 */

import { spawn, type ChildProcess, type SpawnOptions } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { BaseWorker, type Bus } from "@magi/bus";
import { aspBase } from "@magi/runtime/asp.js";

export const ASP_ORIGIN = new URL(aspBase());

export type AspHealth = "ready" | "unavailable" | "incompatible";

export type AspProgress = (message: string, fraction: number) => void;

type SpawnAsp = (command: string, args: readonly string[], options: SpawnOptions) => ChildProcess;

type ProcessSpec = {
  entry: string;
  cwd: string;
  args?: string[];
  env?: NodeJS.ProcessEnv;
};

type ProcessHandle = {
  id: string;
  stop(): void;
  onExit(cb: (code: number | null, signal: string | null) => void): void;
};

type StartProcess = (spec: ProcessSpec) => Promise<ProcessHandle>;

export type AspProcessOptions = {
  origin?: URL;
  sourceCheckout: string;
  node?: string;
  env: NodeJS.ProcessEnv;
  spawnProcess?: SpawnAsp;
  startProcess?: StartProcess;
  initialProcess?: { spec: ProcessSpec; handle: ProcessHandle };
};

export class AspProcessWorker extends BaseWorker {
  readonly worker_name = "asp-process";
  readonly origin: URL;
  private readonly sourceCheckout: string;
  private readonly node: string | undefined;
  private readonly env: NodeJS.ProcessEnv;
  private readonly spawnProcess: SpawnAsp;
  private readonly startProcess: StartProcess | undefined;
  private child: ChildProcess | null = null;
  private handle: ProcessHandle | null = null;
  private checkout: string | null = null;
  private starting: Promise<void> | null = null;
  private wanted = false;
  private wantsShutdown = false;
  private stoppingProcess = false;
  private issue: string | null = null;
  private lastProbeAt = 0;

  constructor(bus: Bus, options: AspProcessOptions) {
    super(bus);
    this.origin = options.origin ?? ASP_ORIGIN;
    this.sourceCheckout = options.sourceCheckout;
    this.node = options.node;
    this.env = { ...options.env };
    for (const key of Object.keys(this.env)) if (key.startsWith("MAGI_DESKTOP_UPDATE_") || ["MAGI_REVISION", "MAGI_REVISION_DIR", "MAGI_REPLACE_JOB", "MAGI_REPLACE_ERROR"].includes(key)) delete this.env[key];
    this.spawnProcess = options.spawnProcess ?? spawn;
    this.startProcess = options.startProcess;
    if (options.initialProcess) {
      this.handle = options.initialProcess.handle;
      this.wanted = true;
      this.handle.onExit((code, signal) => {
        this.handle = null;
        this.issue = `ASP exited (${code ?? signal ?? "unknown"})`;
      });
    }
  }

  requestShutdown(): void {
    this.wantsShutdown = true;
  }

  owned(): boolean {
    if (this.handle !== null) return true;
    return Boolean(this.child && !this.child.killed);
  }

  health(): string | null {
    return this.issue;
  }

  /** Manager start: spawn if prepare already named a checkout, otherwise wait for `ensure`. */
  async start(): Promise<void> {
    if (this.checkout !== null) await this.ensure(this.checkout);
  }

  async stop(): Promise<void> {
    if (!this.wantsShutdown) return;
    this.killOwned();
  }

  async poll(): Promise<boolean> {
    if (!this.wanted && !this.child) return false;
    const now = Date.now();
    if (now - this.lastProbeAt < 1_000) return false;
    this.lastProbeAt = now;
    const health = await this.probe();
    if (health === "ready") this.issue = null;
    else if (this.wanted && this.issue === null) {
      this.issue = health === "incompatible"
        ? `An older ASP is already listening at ${this.origin.href}`
        : "ASP is not running";
    }
    return false;
  }

  async probe(): Promise<AspHealth> {
    try {
      const response = await fetch(new URL("/health", this.origin), { signal: AbortSignal.timeout(400) });
      if (!response.ok) return "unavailable";
      const body: unknown = await response.json();
      if (!body || typeof body !== "object") return "incompatible";
      const status = "status" in body ? body.status : undefined;
      const runtime = "runtime" in body ? body.runtime : undefined;
      return status === "ok" && runtime === "typescript" ? "ready" : "incompatible";
    } catch {
      return "unavailable";
    }
  }

  async ensure(checkout: string, progress?: AspProgress): Promise<void> {
    if (this.wantsShutdown) throw new Error("ASP owner is shutting down");
    if (this.starting) return await this.starting;
    this.starting = this.ensureOnce(checkout, progress);
    try { await this.starting; } finally { this.starting = null; }
  }

  private async ensureOnce(checkout: string, progress?: AspProgress): Promise<void> {
    this.checkout = checkout;
    this.wanted = true;
    progress?.("Checking local ASP…", 0.9);
    const health = await this.probe();
    if (health === "incompatible") {
      throw new Error(`An older ASP is already listening at ${this.origin.href}; quit the old MAGI service before retrying.`);
    }
    if (health === "ready") {
      this.issue = null;
      return;
    }
    const entry = join(checkout, "apps", "eva", "eva.mjs");
    if (!existsSync(entry)) throw new Error(`The ASP launcher is missing: ${entry}`);
    if (this.wantsShutdown) throw new Error("ASP owner is shutting down");
    this.stoppingProcess = false;
    progress?.("Starting local ASP…", 0.9);
    const env = { ...this.env };
    for (const key of ["NODE_TEST_CONTEXT", "MAGI_CHILD", "MAGI_HEALTH", "MAGI_HEALTH_WORKSPACE", "MAGI_APP_CHECKOUT", "MAGI_PREVIOUS_REVISION"]) delete env[key];
    Object.assign(env, { MAGI_ROLE: "asp", MAGI_HOST: "0", MAGI_CHECKOUT: checkout,
      MAGI_NODE: this.nodeBinary(), MAGI_ASP: this.origin.href,
      MAGI_ASP_HOST: this.origin.hostname, MAGI_ASP_PORT: this.origin.port || "42069" });
    try {
      if (!this.owned()) {
        if (this.startProcess) {
          const handle = await this.startProcess({ entry, cwd: dirname(entry), args: ["--role", "asp"], env });
          this.handle = handle;
          handle.onExit((code, signal) => {
            if (this.handle !== handle) return;
            this.handle = null;
            if (!this.stoppingProcess) this.issue = `ASP exited (${code ?? signal ?? "unknown"})`;
          });
        } else this.child = this.spawnAsp(entry, env);
      }
      if (this.wantsShutdown) throw new Error("ASP owner is shutting down");
      await this.waitUntilReady();
      if (this.wantsShutdown) throw new Error("ASP owner is shutting down");
      this.issue = null;
    } catch (error) {
      this.killOwned();
      this.issue = error instanceof Error ? error.message : String(error);
      throw error;
    }
  }

  async stopOwned(): Promise<void> {
    if (await this.probe() !== "ready") return;
    this.stoppingProcess = true;
    if (this.handle !== null) {
      this.handle.stop();
    } else if (this.child && !this.child.killed) {
      this.child.kill("SIGTERM");
    } else {
      await this.requestStop();
    }
    for (let attempt = 0; attempt < 50; attempt++) {
      if (await this.probe() === "unavailable") {
        this.child = null;
        this.handle = null;
        this.wanted = false;
        this.issue = null;
        return;
      }
      await sleep(200);
    }
    throw new Error("ASP did not stop.");
  }

  private spawnAsp(entry: string, env: NodeJS.ProcessEnv): ChildProcess {
    const child = this.spawnProcess(this.nodeBinary(), [entry, "--role", "asp"], {
      cwd: dirname(entry),
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.once("error", (error) => { if (this.child === child) this.child = null; this.issue = error.message; });
    child.stderr?.on("data", (chunk) => {
      const text = String(chunk).trim();
      if (text) console.error("[asp]", text);
    });
    child.once("exit", (code, signal) => {
      if (this.stoppingProcess) return;
      if (this.child !== child) return;
      this.child = null;
      this.issue = `ASP exited (${code ?? signal ?? "unknown"})`;
    });
    return child;
  }

  private async waitUntilReady(timeoutMs = 20_000): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (this.wantsShutdown) throw new Error("ASP owner is shutting down");
      if (!this.owned()) throw new Error(this.issue || "ASP exited before becoming ready");
      const health = await this.probe();
      if (health === "ready") return;
      if (health === "incompatible") {
        throw new Error(`An older ASP is already listening at ${this.origin.href}; quit the old MAGI service before retrying.`);
      }
      await sleep(200);
    }
    throw new Error(`ASP is unavailable at ${new URL("/health", this.origin).href}`);
  }

  private killOwned(): void {
    this.stoppingProcess = true;
    if (this.handle !== null) this.handle.stop();
    else if (this.child && !this.child.killed) this.child.kill("SIGTERM");
    this.handle = null;
    this.child = null;
    this.wanted = false;
    this.issue = null;
  }

  private nodeBinary(): string {
    if (typeof this.node === "string" && this.node !== "node" && existsSync(this.node)) return this.node;
    const bundled = join(
      this.sourceCheckout,
      "apps",
      "shell",
      "runtime",
      "bin",
      process.platform === "win32" ? "node.exe" : "node",
    );
    if (existsSync(bundled)) return bundled;
    return this.node ?? "node";
  }

  private async requestStop(): Promise<void> {
    const operator = await this.json("/operator");
    const token = operator && typeof operator === "object" && typeof operator.token === "string" ? operator.token : "";
    await this.json("/runtime/asp/stop", { token, method: "POST" });
  }

  private async json(endpoint: string, { token, method = "GET" }: { token?: string; method?: string } = {}): Promise<{ token?: string }> {
    const response = await fetch(new URL(endpoint, this.origin), {
      method,
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      signal: AbortSignal.timeout(20_000),
    });
    const payload = await response.text();
    let data: { token?: string; detail?: string } | null = null;
    if (payload !== "") {
      try { data = JSON.parse(payload) as { token?: string; detail?: string }; }
      catch { data = { detail: payload.slice(0, 300) }; }
    }
    if (!response.ok) {
      throw new Error(`ASP ${method} ${endpoint} failed (${response.status}): ${data?.detail ?? response.statusText}`);
    }
    return data ?? {};
  }
}
