/**
 * Rebuild this app and replace the process. The build runs off the poll loop.
 *
 * Order: snapshot every file in the app's dist, build, drain, stop the other
 * workers, start the new build's workers and health-check them, and only then
 * mark the job done. A failure restores that dist. The requesting agent is
 * woken with the error and log tail (capped); the operator is told after the cap.
 * The running process is never left beside a second one: a supervised app
 * asks its supervisor to restart it, and a hand-run app exits after spawning.
 */

import { createHash } from "node:crypto";
import { execFile, fork } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { BaseWorker, SYSTEM_CONTACT_ID, messageDelivery, replaceFailedPath, type Bus } from "@magi/bus";
import { LAUNCHER_PROTOCOL } from "@magi/runtime/protocol.js";
import { packageDesktopRevision, type DesktopManifest } from "@magi/runtime/desktop-package.js";
import { reviseCheckout } from "@magi/runtime/revise.js";
import { resolveToolchain } from "@magi/runtime/toolchain.js";

const GATE_TRIES = 3;
const LOG_TAIL = 8_000;
const FAILURES_KEY = "replace.gate_failures";
type ReplaceFailKind = "gate" | "crash" | "build";

export type AppBuild = {
  app: "eva" | "asp" | "shell";
  /** `package.json` script in `apps/<app>`, such as `build` or `build:process`. */
  script: string;
};

export type ReplaceHost = {
  suspendForReplace(): Promise<void>;
  resume(): Promise<void>;
  healthy(): string | null;
  installDesktopUpdate?(manifest: DesktopManifest, job: number): Promise<unknown>;
};

const HEALTH_MS = 20_000;

export class ReplaceWorker extends BaseWorker {
  readonly worker_name = "replace";
  private task: Promise<void> | null = null;
  private desktopHandoff = false;

  constructor(bus: Bus, private readonly build: AppBuild, private readonly host: ReplaceHost) {
    super(bus);
  }

  async poll(): Promise<boolean> {
    if (this.desktopHandoff) return false;
    if (process.env.MAGI_REPLACE_JOB || process.env.MAGI_REPLACE_ERROR) return this.finishHandoff();
    if (this.noteFailedReplace()) return true;
    if (this.consumeReplaceMarker()) return true;
    if (this.task) return false;
    const board = this.bus.board("ReplaceSelfJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    const id = job.id;
    const input = job.input;
    this.task = this.execute(id, input.checkout, input.ref, input.swap === true, input.chat_id, input.desktop === true).finally(() => { this.task = null; });
    return true;
  }

  private async execute(id: number, requested: string | undefined, ref: string | undefined, swap: boolean, chatId?: number, desktop = false): Promise<void> {
    const board = this.bus.board("ReplaceSelfJob");
    const checkout = requested ?? findCheckout();
    if (swap) {
      await this.switchRevision(id, checkout, ref, chatId, desktop);
      return;
    }
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
      await healthCheck(this.build.app === "eva" ? "agent" : this.build.app);
      board.submit(this.worker_name, id, { output: { replaced: true } });
      rmSync(backup, { recursive: true, force: true });
      gateSuccess(this.bus, chatId);
      tell(this.bus, "[replace] replaced");
      await this.host.resume();
    } catch (error) {
      restore(dist, backup);
      let text = message(error);
      if (suspended) {
        try { await this.host.resume(); }
        catch (resumeError) { text = `${text}; ${message(resumeError)}`; }
      }
      board.submit(this.worker_name, id, { error: text });
      failReplace(this.bus, text, chatId, suspended ? "crash" : "build");
    }
  }

  /** Build the target revision in its own directory, then ask the launcher to run it. */
  private async switchRevision(id: number, checkout: string, ref: string | undefined, chatId?: number, desktop = false): Promise<void> {
    const board = this.bus.board("ReplaceSelfJob");
    if (typeof process.send !== "function" || !process.connected) {
      const text = "this node has no launcher to switch to the new revision";
      board.submit(this.worker_name, id, { error: text });
      failReplace(this.bus, text, chatId, "crash");
      return;
    }
    const target = ref?.trim() || undefined;
    let built: { revision: string; previous: string; dir: string };
    let desktopManifest: DesktopManifest | undefined;
    try {
      if (desktop && (!this.host.installDesktopUpdate || this.build.app !== "shell" || process.env.MAGI_PACKAGED !== "1" || process.platform !== "linux")) throw new Error("This node cannot replace the desktop package");
      built = await reviseCheckout(checkout, target);
      if (desktop) desktopManifest = await packageDesktopRevision(built);
    } catch (error) {
      const text = message(error);
      board.submit(this.worker_name, id, { error: text });
      failReplace(this.bus, text, chatId, "gate");
      return;
    }
    try {
      if (desktopManifest) {
        this.bus.settings.set("replace.pending", String(id));
        this.desktopHandoff = true;
        await this.host.installDesktopUpdate!(desktopManifest, id);
        return;
      }
      const launcher = join(built.dir, "apps", "eva", "eva.mjs");
      const running = process.env.MAGI_LAUNCHER;
      const upgrade = Boolean(running && existsSync(running) && existsSync(launcher) && fileHash(running) !== fileHash(launcher));
      this.bus.settings.set("replace.pending", String(id));
      process.send({
        v: LAUNCHER_PROTOCOL,
        type: upgrade ? "upgrade" : "switch",
        revision: built.revision,
        previous: built.previous,
        dir: built.dir,
        job: id,
        launcher,
      });
      const outcome = await waitLauncher();
      if (outcome === "shutdown") return;
      throw new Error(outcome);
    } catch (error) {
      this.desktopHandoff = false;
      const text = message(error);
      board.submit(this.worker_name, id, { error: text });
      failReplace(this.bus, text, chatId, "crash");
    }
  }

  /**
   * This process is the one the supervisor just started. Submit the replace
   * only after its workers are healthy. An unhealthy replacement stays silent
   * so healthyOrExit can end it and the shell can start the old process.
   */
  private finishHandoff(): boolean {
    const jobId = Number(process.env.MAGI_REPLACE_JOB);
    if (!Number.isInteger(jobId)) return false;
    const replaceError = process.env.MAGI_REPLACE_ERROR;
    if (!replaceError && this.host.healthy() !== null) return false;
    if (!replaceError && process.env.MAGI_DESKTOP_UPDATE_JOURNAL) {
      try {
        const journal = JSON.parse(readFileSync(process.env.MAGI_DESKTOP_UPDATE_JOURNAL, "utf8"));
        if (journal.status !== "completed" || journal.job !== jobId || journal.manifest.revision !== process.env.MAGI_REVISION) return false;
      } catch { return false; }
    }
    const board = this.bus.board("ReplaceSelfJob");
    const done = board.result(jobId);
    if (done) {
      this.clearHandoff();
      return true;
    }
    const job = board.claimId(this.worker_name, jobId);
    if (!job) return false;
    if (replaceError) {
      board.submit(this.worker_name, job.id, { error: replaceError });
      failReplace(this.bus, replaceError, job.input.chat_id, "crash");
    } else {
      const dist = join(job.input.checkout ?? findCheckout(), "apps", this.build.app, "dist");
      rmSync(`${dist}.rollback`, { recursive: true, force: true });
      board.submit(this.worker_name, job.id, { output: { replaced: true } });
      gateSuccess(this.bus, job.input.chat_id);
      tell(this.bus, "[replace] replaced");
    }
    this.clearHandoff();
    return true;
  }

  private clearHandoff(): void {
    delete process.env.MAGI_REPLACE_JOB;
    delete process.env.MAGI_REPLACE_ERROR;
    delete process.env.MAGI_DESKTOP_UPDATE_JOURNAL;
    this.bus.settings.set("replace.pending", "");
  }

  /** The shell could not start the old process. Fail that job, then drop the marker. */
  private consumeReplaceMarker(): boolean {
    const marker = replaceFailedPath(this.bus.handle);
    if (!existsSync(marker)) return false;
    let jobId: number | null = null;
    let error = "replace failed";
    try {
      const body = JSON.parse(readFileSync(marker, "utf8")) as { job?: unknown; error?: unknown };
      if (typeof body.error === "string" && body.error !== "") error = body.error;
      if (typeof body.job === "number" && Number.isInteger(body.job)) jobId = body.job;
    } catch {
      tell(this.bus, "[replace] replace marker could not be read");
      rmSync(marker, { force: true });
      return true;
    }
    const board = this.bus.board("ReplaceSelfJob");
    if (jobId !== null && board.result(jobId)) {
      rmSync(marker, { force: true });
      return true;
    }
    if (jobId !== null) board.fail(jobId, error);
    failReplace(this.bus, error, jobId !== null ? board.peekInput(jobId)?.chat_id : undefined, "crash");
    rmSync(marker, { force: true });
    return true;
  }

  /** The previous process died during a replace. Say so once. */
  private noteFailedReplace(): boolean {
    const pending = this.bus.settings.get("replace.pending");
    if (!pending) return false;
    if (process.env.MAGI_REPLACE_JOB) return false;
    const id = Number(pending);
    const board = this.bus.board("ReplaceSelfJob");
    const result = board.result(id);
    this.bus.settings.set("replace.pending", "");
    if (result) return true;
    const error = "MAGI crashed before this replace finished";
    const chatId = board.peekInput(id)?.chat_id;
    board.fail(id, error);
    failReplace(this.bus, error, chatId, "crash");
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

function nodeBinary(): string {
  return resolveToolchain().node;
}

function runBuild(appDir: string, script: string): Promise<void> {
  return exec(nodeBinary(), ["--run", script], appDir);
}

function checkEntry(entry: string): Promise<void> {
  if (!existsSync(entry)) return Promise.reject(new Error(`build produced no ${entry}`));
  return exec(nodeBinary(), ["--check", entry], dirname(entry));
}

function exec(file: string, args: string[], cwd: string): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { cwd }, (error, stdout, stderr) => {
      if (error) reject(new Error(`${stderr}${stdout}`.trim() || error.message));
      else resolve();
    });
  });
}

/**
 * Boot the new entry in a throwaway workspace. It must not be given the relay
 * token: ASP is checked later, in the replacement process, with the real identity.
 */
function childScript(): string {
  const override = process.env.MAGI_CHILD?.trim();
  if (override) return override;
  return join(dirname(fileURLToPath(import.meta.url)), "..", "..", "node", "dist", "child.js");
}

function healthCheck(role: "agent" | "asp" | "shell"): Promise<void> {
  const workspace = mkdtempSync(join(tmpdir(), "magi-health-"));
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    MAGI_HEALTH: "1",
    MAGI_HEALTH_WORKSPACE: workspace,
    MAGI_ROLE: role,
  };
  delete env.MAGI_HEALTH_ASP_BASE;
  delete env.MAGI_HEALTH_ASP_TOKEN;
  delete env.MAGI_REPLACE_JOB;
  delete env.MAGI_REPLACE_ERROR;
  const entry = childScript();
  const child = fork(entry, ["--role", role], {
    execPath: nodeBinary(),
    execArgv: [],
    cwd: dirname(entry),
    env,
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

function fileHash(file: string): string {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

/** Shutdown means the launcher took over. Anything else is a failure the job must record. */
function waitLauncher(): Promise<string> {
  return new Promise((resolve) => {
    const onMessage = (message: unknown) => {
      const body = message as { v?: number; type?: string; error?: string };
      if (body?.v !== LAUNCHER_PROTOCOL) return;
      if (body.type === "shutdown") {
        process.off("message", onMessage);
        resolve("shutdown");
        return;
      }
      if (body.type === "switch-failed" || body.type === "upgrade-failed") {
        process.off("message", onMessage);
        resolve(body.error || "switch failed");
      }
    };
    process.on("message", onMessage);
  });
}

function failuresKey(chatId: number): string {
  return `${FAILURES_KEY}.${chatId}`;
}

function gateSuccess(bus: Bus, chatId?: number): void {
  if (typeof chatId === "number" && Number.isInteger(chatId) && chatId > 0) {
    bus.settings.set(failuresKey(chatId), "");
  }
}

function failCopy(kind: ReplaceFailKind, attempt?: number): string {
  if (kind === "gate") {
    return attempt === undefined
      ? `safety gate failed after ${GATE_TRIES} attempts. The running process was not replaced.`
      : `safety gate failed (attempt ${attempt}/${GATE_TRIES}). The running process was not replaced.`;
  }
  if (kind === "build") {
    return attempt === undefined
      ? `the process was not replaced after ${GATE_TRIES} attempts (build failure).`
      : `the process was not replaced (build failure, attempt ${attempt}/${GATE_TRIES}).`;
  }
  return attempt === undefined
    ? `the process was not replaced after ${GATE_TRIES} attempts (launcher rollback or crash).`
    : `the process was not replaced (launcher rollback or crash, attempt ${attempt}/${GATE_TRIES}).`;
}

function failReplace(bus: Bus, error: string, chatId: number | undefined, kind: ReplaceFailKind): void {
  const tail = error.length > LOG_TAIL ? error.slice(-LOG_TAIL) : error;
  const chat = typeof chatId === "number" && Number.isInteger(chatId) && chatId > 0 ? chatId : 0;
  const live = chat > 0 ? bus.chats.get(chat) : null;
  if (!live) {
    // No requesting chat: crash keeps the original operator marker text.
    if (kind === "build") tell(bus, `[replace] the process was not replaced (build failure).\n\n${tail}`);
    else tell(bus, `[replace] ${tail}`);
    return;
  }
  const attempt = (Number(bus.settings.get(failuresKey(chat))) || 0) + 1;
  bus.settings.set(failuresKey(chat), String(attempt));
  if (attempt <= GATE_TRIES) {
    messageDelivery.send(bus, chat, `[replace] ${failCopy(kind, attempt)}\n\n${tail}`, SYSTEM_CONTACT_ID, "replace");
    return;
  }
  tell(bus, `[replace] ${failCopy(kind)}\n\n${tail}`);
}

function tell(bus: Bus, text: string): void {
  if (messageDelivery.notify(bus, text) !== null) return;
  bus.board("ManageWorkerNotify").seal("replace", { worker: "replace", action: "restart" }, text);
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
