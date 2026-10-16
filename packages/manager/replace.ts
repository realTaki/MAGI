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
};

const HEALTH_MS = 20_000;

export class ReplaceWorker extends BaseWorker {
  readonly worker_name = "replace";
  private task: Promise<void> | null = null;

  constructor(bus: Bus, private readonly build: AppBuild, private readonly host: ReplaceHost) {
    super(bus);
  }

  async poll(): Promise<boolean> {
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
      await healthCheck(entry);
      board.submit(this.worker_name, id, { output: { replaced: true } });
      tell(this.bus, "[replace] replaced");
      if (!swap) {
        await this.host.resume();
        return;
      }
      if (typeof process.send === "function" && process.connected) {
        process.send({ type: "runtime.replace" });
        return;
      }
      this.bus.close();
      const child = spawn(process.execPath, [entry, ...process.argv.slice(2)], {
        cwd: dirname(entry),
        env: process.env,
        stdio: "inherit",
      });
      child.unref();
      process.exit(0);
    } catch (error) {
      restore(dist, backup);
      const text = message(error);
      board.submit(this.worker_name, id, { error: text });
      tell(this.bus, `[replace] ${text}`);
      if (suspended) {
        try { await this.host.resume(); }
        catch (resumeError) { tell(this.bus, `[replace] ${message(resumeError)}`); }
      }
    }
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
function healthCheck(entry: string): Promise<void> {
  const workspace = mkdtempSync(join(tmpdir(), "magi-health-"));
  const child = fork(entry, ["@health.magi"], {
    execPath: process.execPath,
    execArgv: [],
    cwd: dirname(entry),
    env: { ...process.env, MAGI_HEALTH: "1", MAGI_HEALTH_WORKSPACE: workspace },
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
  const delivered = messageDelivery.notify(bus, text);
  if (delivered === null) console.error(text);
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
