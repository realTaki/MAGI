/**
 * Rebuild this app and replace the process. The build runs off the poll loop.
 *
 * Order: snapshot every file in the app's dist, build, drain, stop the other
 * workers, start the new build's workers and health-check them, and only then
 * mark the job done. A failure restores that dist and tells the operator.
 * The running process is never left beside a second one: a supervised app
 * asks its supervisor to restart it, and a hand-run app exits after spawning.
 */

import { execFile, fork, spawn } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { BaseWorker, messageDelivery, type Bus } from "@magi/bus";

export type AppBuild = {
  app: "eva" | "asp" | "shell";
  /** `package.json` script in `apps/<app>`, such as `build` or `build:process`. */
  script: string;
};

export type ReplaceHost = {
  suspendForReplace(): Promise<void>;
  resume(): Promise<void>;
  healthy(): string | null;
};

const HEALTH_MS = 20_000;

export class ReplaceWorker extends BaseWorker {
  readonly worker_name = "replace";
  private task: Promise<void> | null = null;
  private handoffDeadline = 0;

  constructor(bus: Bus, private readonly build: AppBuild, private readonly host: ReplaceHost) {
    super(bus);
  }

  async poll(): Promise<boolean> {
    if (process.env.MAGI_REPLACE_JOB || process.env.MAGI_REPLACE_ERROR) return this.finishHandoff();
    if (this.noteFailedReplace()) return true;
    if (this.task) return false;
    const board = this.bus.board("ReplaceSelfJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    const id = job.id;
    const input = job.input;
    this.task = this.execute(id, input.checkout, input.swap === true).finally(() => { this.task = null; });
    return true;
  }

  private async execute(id: number, requested: string | undefined, swap: boolean): Promise<void> {
    const board = this.bus.board("ReplaceSelfJob");
    const checkout = requested ?? findCheckout();
    const appDir = join(checkout, "apps", this.build.app);
    const dist = join(appDir, "dist");
    const backup = `${dist}.rollback`;
    const entry = join(dist, `${this.build.app}.js`);
    let suspended = false;
    try {
      snapshot(dist, backup);
      await runBuild(appDir, this.build.script);
      await checkEntry(entry);
      await this.host.suspendForReplace();
      suspended = true;
      const base = this.bus.settings.get("asp.base")?.trim();
      const token = this.bus.settings.get("asp.token")?.trim();
      await healthCheck(entry, base && token ? { base, token } : undefined);
      if (!swap) {
        board.submit(this.worker_name, id, { output: { replaced: true } });
        rmSync(backup, { recursive: true, force: true });
        tell(this.bus, "[replace] replaced");
        await this.host.resume();
        return;
      }
      // The job stays open until the process that takes over is healthy.
      this.bus.settings.set("replace.pending", String(id));
      if (typeof process.send === "function" && process.connected) {
        const refused = await askSupervisor(id);
        if (refused) {
          this.bus.settings.set("replace.pending", "");
          throw new Error(refused);
        }
        return;
      }
      this.bus.close();
      const child = spawn(process.execPath, [entry, ...process.argv.slice(2)], {
        cwd: dirname(entry),
        env: { ...process.env, MAGI_REPLACE_JOB: String(id) },
        stdio: "inherit",
      });
      child.unref();
      process.exit(0);
    } catch (error) {
      restore(dist, backup);
      let text = message(error);
      if (suspended) {
        try { await this.host.resume(); }
        catch (resumeError) { text = `${text}; ${message(resumeError)}`; }
      }
      board.submit(this.worker_name, id, { error: text });
      tell(this.bus, `[replace] ${text}`);
    }
  }

  /**
   * This process is the one the supervisor just started. Submit the replace
   * only after its workers are healthy; otherwise put the previous dist back.
   */
  private finishHandoff(): boolean {
    const replaceError = process.env.MAGI_REPLACE_ERROR;
    if (!replaceError) {
      if (this.handoffDeadline === 0) this.handoffDeadline = Date.now() + 8_000;
      if (this.host.healthy() && Date.now() < this.handoffDeadline) return false;
    }
    const board = this.bus.board("ReplaceSelfJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    const dist = join(job.input.checkout ?? findCheckout(), "apps", this.build.app, "dist");
    const backup = `${dist}.rollback`;
    const problem = replaceError || this.host.healthy();
    if (problem) {
      if (existsSync(backup)) restore(dist, backup);
      board.submit(this.worker_name, job.id, { error: problem });
      tell(this.bus, `[replace] ${problem}`);
    } else {
      board.submit(this.worker_name, job.id, { output: { replaced: true } });
      rmSync(backup, { recursive: true, force: true });
      tell(this.bus, "[replace] replaced");
    }
    delete process.env.MAGI_REPLACE_JOB;
    delete process.env.MAGI_REPLACE_ERROR;
    this.bus.settings.set("replace.pending", "");
    return true;
  }

  /** The previous process died during a replace. Say so once. */
  private noteFailedReplace(): boolean {
    const pending = this.bus.settings.get("replace.pending");
    if (!pending) return false;
    const result = this.bus.board("ReplaceSelfJob").result(Number(pending));
    if (!result) return false;
    this.bus.settings.set("replace.pending", "");
    if (result.status === "failed") tell(this.bus, `[replace] ${result.error ?? "replace failed"}`);
    return true;
  }
}

function findCheckout(): string {
  const start = process.argv[1] ? dirname(process.argv[1]) : process.cwd();
  let dir = start;
  while (true) {
    if (existsSync(join(dir, "package.json")) && existsSync(join(dir, "apps"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error("this MAGI has no source checkout");
}

function snapshot(live: string, backup: string): void {
  rmSync(backup, { recursive: true, force: true });
  if (existsSync(live)) cpSync(live, backup, { recursive: true });
}

function restore(live: string, backup: string): void {
  rmSync(live, { recursive: true, force: true });
  if (existsSync(backup)) cpSync(backup, live, { recursive: true });
  rmSync(backup, { recursive: true, force: true });
}

function runBuild(appDir: string, script: string): Promise<void> {
  return exec(process.execPath, ["--run", script], appDir);
}

function checkEntry(entry: string): Promise<void> {
  if (!existsSync(entry)) return Promise.reject(new Error(`build produced no ${entry}`));
  return exec(process.execPath, ["--check", entry], dirname(entry));
}

function exec(file: string, args: string[], cwd: string): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { cwd }, (error, stdout, stderr) => {
      if (error) reject(new Error(`${stderr}${stdout}`.trim() || error.message));
      else resolve();
    });
  });
}

/** Start the new entry's workers in a throwaway workspace and require them to come up healthy. */
function healthCheck(entry: string, asp?: { base: string; token: string }): Promise<void> {
  const workspace = mkdtempSync(join(tmpdir(), "magi-health-"));
  const child = fork(entry, ["@health.magi"], {
    execPath: process.execPath,
    execArgv: [],
    cwd: dirname(entry),
    env: {
      ...process.env,
      MAGI_HEALTH: "1",
      MAGI_HEALTH_WORKSPACE: workspace,
      ...(asp ? { MAGI_HEALTH_ASP_BASE: asp.base, MAGI_HEALTH_ASP_TOKEN: asp.token } : {}),
    },
    stdio: ["ignore", "pipe", "pipe", "ipc"],
  });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("replacement workers did not become healthy"));
    }, HEALTH_MS);
    let stderr = "";
    child.stderr?.on("data", (chunk) => { stderr += String(chunk); });
    child.once("exit", (code) => {
      clearTimeout(timer);
      rmSync(workspace, { recursive: true, force: true });
      if (code === 0) resolve();
      else reject(new Error(stderr.trim() || `replacement workers exited ${code ?? "null"}`));
    });
  });
}

function tell(bus: Bus, text: string): void {
  if (messageDelivery.notify(bus, text) !== null) return;
  bus.board("ManageWorkerNotify").seal("replace", { worker: "replace", action: "restart" }, text);
}

/** Ask the supervisor to restart this process. An error string means it refused or the restart failed. Null means this process is being replaced. */
function askSupervisor(id: number): Promise<string | null> {
  process.send?.({ type: "runtime.replace", job: id });
  return new Promise((resolve) => {
    let accepted = false;
    let timer = setTimeout(onTimeout, 15_000);
    timer.unref();
    function finish(result: string | null) {
      clearTimeout(timer);
      process.off("message", onMessage);
      resolve(result);
    }
    function onTimeout() {
      finish(accepted ? "shell did not finish the replace" : "shell did not accept the replace");
    }
    const onMessage = (body: { type?: string; error?: string }) => {
      if (body?.type === "shutdown") {
        finish(null);
        return;
      }
      if (body?.type !== "runtime.replace.accepted") return;
      if (typeof body.error === "string" && body.error !== "") {
        finish(body.error);
        return;
      }
      if (accepted) return;
      accepted = true;
      clearTimeout(timer);
      timer = setTimeout(onTimeout, 120_000);
      timer.unref();
    };
    process.on("message", onMessage);
    process.once("SIGTERM", () => finish(null));
    process.once("SIGINT", () => finish(null));
  });
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
