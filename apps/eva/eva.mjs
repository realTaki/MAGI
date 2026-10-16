/**
 * eva is the launcher. It is not a BUS. One child process is the node.
 * Shell, ASP, and a cloud agent differ only by --role.
 *
 * A revision is a directory under MAGI_HOME. The child is that directory's
 * packages/node/dist/child.js, so a switch runs that revision's code.
 */

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { magiHome } from "@magi/runtime/home.js";
import { LAUNCHER_PROTOCOL } from "@magi/runtime/protocol.js";
import { resolveToolchain } from "@magi/runtime/toolchain.js";

const launcherFile = fileURLToPath(import.meta.url);
const bundledChild = process.env.MAGI_CHILD
  || fileURLToPath(new URL("../../packages/node/dist/child.js", import.meta.url));

const CRASH_LIMIT = Number(process.env.MAGI_CRASH_LIMIT) || 5;
const CRASH_BACKOFF_MS = Number(process.env.MAGI_CRASH_BACKOFF_MS) || 1000;

function args() {
  const roleFlag = process.argv.indexOf("--role");
  const role = roleFlag >= 0 ? process.argv[roleFlag + 1] : (process.env.MAGI_ROLE || "agent");
  const rest = process.argv.slice(2).filter((arg, index, all) => arg !== "--role" && all[index - 1] !== "--role");
  return { role, rest };
}

function nodeName(role, rest) {
  if (role === "shell") return "eva-000";
  if (role === "asp") return "eva-001";
  const handle = rest[0] || "agent";
  return handle.replace(/^@/, "").replace(/\.magi$/, "");
}

function statePath(name) {
  return join(magiHome(), name, "launcher.json");
}

function markerPath(name) {
  return join(magiHome(), name, "replace-failed.json");
}

function readState(name) {
  const empty = { protocol: LAUNCHER_PROTOCOL, current: null, previous: null, currentDir: null, previousDir: null };
  try {
    const body = JSON.parse(readFileSync(statePath(name), "utf8"));
    if (body?.protocol !== LAUNCHER_PROTOCOL) return empty;
    return { ...empty, ...body };
  } catch {
    return empty;
  }
}

function writeState(name, state) {
  const file = statePath(name);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, JSON.stringify({
    protocol: LAUNCHER_PROTOCOL,
    current: state.current ?? null,
    previous: state.previous ?? null,
    currentDir: state.currentDir ?? null,
    previousDir: state.previousDir ?? null,
  }));
}

function writeMarker(name, error, job = null) {
  const file = markerPath(name);
  mkdirSync(dirname(file), { recursive: true });
  const id = Number.isInteger(job) ? job : null;
  writeFileSync(file, JSON.stringify({ job: id, error }));
}

function report(name, error, job = null) {
  const text = error instanceof Error ? error.message : String(error);
  writeMarker(name, text, job);
  console.error(text);
  if (typeof process.send === "function") {
    try { process.send({ v: LAUNCHER_PROTOCOL, type: "failed", error: text }); } catch { /* parent already gone */ }
  }
  return text;
}

function childScript(name, dir) {
  if (!dir) return bundledChild;
  const root = resolve(magiHome(), name, "revisions");
  const resolved = resolve(dir);
  if (resolved !== root && !resolved.startsWith(root + sep)) {
    throw new Error("revision directory is outside MAGI_HOME");
  }
  const script = join(resolved, "packages", "node", "dist", "child.js");
  if (!existsSync(script)) throw new Error(`revision has no child at ${script}`);
  return script;
}

function spawnChild(role, rest, name, { script, revision, replaceJob, replaceError }) {
  const { node } = resolveToolchain();
  const host = process.env.MAGI_HOST === "1";
  const env = {
    ...process.env,
    MAGI_ROLE: role,
    MAGI_HOME: magiHome(),
    MAGI_NODE_NAME: name,
    MAGI_LAUNCHER: launcherFile,
    ...(rest[0] ? { MAGI_HANDLE: rest[0] } : {}),
    ...(rest[1] ? { MAGI_ASP: rest[1] } : {}),
    ...(rest[2] ? { MAGI_ASP_TOKEN: rest[2] } : {}),
    ...(revision ? { MAGI_REVISION: revision } : {}),
  };
  delete env.MAGI_REPLACE_JOB;
  delete env.MAGI_REPLACE_ERROR;
  if (Number.isInteger(replaceJob)) env.MAGI_REPLACE_JOB = String(replaceJob);
  if (replaceError) env.MAGI_REPLACE_ERROR = replaceError;
  const child = spawn(node, [script, "--role", role, ...rest], {
    env,
    stdio: host ? ["pipe", "pipe", "pipe", "ipc"] : ["ignore", "inherit", "inherit", "ipc"],
    windowsHide: true,
  });
  if (host) {
    process.stdin.unpipe();
    process.stdin.pipe(child.stdin);
    child.stdout.pipe(process.stdout);
    child.stderr.pipe(process.stderr);
  }
  note(child);
  child.send({ v: LAUNCHER_PROTOCOL, type: "hello" });
  return child;
}

function note(child) {
  child.seen = { hello: false, ready: false, bad: "" };
  child.on("message", (message) => {
    if (message?.type === "hello" || message?.type === "ready") {
      if (message.v !== LAUNCHER_PROTOCOL) child.seen.bad = `launcher protocol ${LAUNCHER_PROTOCOL} does not match child ${message.v ?? "none"}`;
      else child.seen[message.type] = true;
    }
  });
}

function waitFor(child, type, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(type === "hello" ? "child protocol handshake timed out" : "child did not become ready")), timeoutMs);
    const tick = () => {
      if (child.seen?.bad) {
        clearTimeout(timer);
        reject(new Error(child.seen.bad));
        return;
      }
      if (child.seen?.[type]) {
        clearTimeout(timer);
        resolve();
        return;
      }
      if (child.exitCode != null || child.signalCode != null) {
        clearTimeout(timer);
        reject(new Error(`child exited ${child.exitCode ?? child.signalCode ?? "null"}`));
        return;
      }
      setTimeout(tick, 10);
    };
    tick();
  });
}

async function waitHealthy(child) {
  await waitFor(child, "hello", 10_000);
  await waitFor(child, "ready", 30_000);
}

function stopChild(child) {
  if (!child || child.exitCode != null || child.signalCode != null) return;
  if (child.connected) child.send({ v: LAUNCHER_PROTOCOL, type: "shutdown" });
  else child.kill("SIGTERM");
}

function stopAndWait(child) {
  if (!child || child.exitCode != null || child.signalCode != null) return Promise.resolve();
  stopChild(child);
  return new Promise((resolve) => {
    const timer = setTimeout(() => { child.kill("SIGKILL"); }, 5_000);
    child.once("exit", () => { clearTimeout(timer); resolve(); });
  });
}

function backoff(attempt) {
  return Math.min(CRASH_BACKOFF_MS * 2 ** (attempt - 1), 30_000);
}

async function main() {
  const { role, rest } = args();
  const name = nodeName(role, rest);
  const state = readState(name);
  if (process.env.MAGI_REVISION_DIR) state.currentDir = process.env.MAGI_REVISION_DIR;
  if (process.env.MAGI_REVISION) state.current = process.env.MAGI_REVISION;

  let replaced = false;
  let acceptingCrashes = false;
  let crashes = 0;
  let current = null;

  const launch = (options) => {
    const script = options.script ?? childScript(name, options.dir ?? null);
    const child = spawnChild(role, rest, name, { ...options, script });
    const started = Date.now();
    child.on("message", (message) => { void onMessage(message); });
    child.on("exit", () => { void onExit(child, started); });
    return child;
  };

  const onExit = async (child, started) => {
    if (!acceptingCrashes || replaced || child !== current) return;
    if (Date.now() - started > 30_000) crashes = 0;
    crashes += 1;
    if (crashes > CRASH_LIMIT) {
      report(name, `child crashed ${crashes} times; restart stopped`);
      process.exit(1);
    }
    await new Promise((resolve) => setTimeout(resolve, backoff(crashes)));
    if (replaced || child !== current) return;
    try {
      current = launch({ dir: state.currentDir, revision: state.current });
      await waitHealthy(current);
    } catch {
      if (current && current.exitCode == null && current.signalCode == null) current.kill("SIGTERM");
    }
  };

  const onMessage = async (message) => {
    if (message?.v !== LAUNCHER_PROTOCOL) return;
    if (message.type === "upgrade") {
      await handoff(message);
      return;
    }
    if (message.type !== "switch") return;
    await switchTo(message);
  };

  async function switchTo(message) {
    replaced = true;
    const job = Number.isInteger(message.job) ? message.job : null;
    const previousDir = state.currentDir;
    const old = current;
    let next = null;
    try {
      const script = childScript(name, message.dir);
      await stopAndWait(old);
      next = launch({ script, revision: message.revision, replaceJob: job, dir: message.dir });
      current = next;
      await waitHealthy(next);
      state.previous = state.current;
      state.previousDir = previousDir;
      state.current = String(message.revision ?? "");
      state.currentDir = message.dir;
      writeState(name, state);
    } catch (error) {
      if (next) next.kill("SIGTERM");
      const text = report(name, error, job);
      try {
        current = launch({
          dir: previousDir,
          revision: state.current,
          replaceError: text,
        });
        await waitHealthy(current);
      } catch (rollbackError) {
        report(name, `${text}; rollback failed: ${rollbackError instanceof Error ? rollbackError.message : rollbackError}`, job);
        process.exit(1);
      }
    } finally {
      replaced = false;
    }
  }

  async function handoff(message) {
    replaced = true;
    const job = Number.isInteger(message.job) ? message.job : null;
    const launcher = typeof message.launcher === "string" ? message.launcher : "";
    if (!launcher || !existsSync(launcher)) {
      report(name, "the new launcher is missing", job);
      replaced = false;
      return;
    }
    await stopAndWait(current);
    current = null;
    const { node } = resolveToolchain();
    const host = process.env.MAGI_HOST === "1";
    const next = spawn(node, [launcher, "--role", role, ...rest], {
      env: {
        ...process.env,
        MAGI_HANDOFF: "1",
        MAGI_HOST: process.env.MAGI_HOST ?? "",
        MAGI_REVISION: String(message.revision ?? ""),
        MAGI_REVISION_DIR: String(message.dir ?? ""),
        MAGI_HOME: magiHome(),
        MAGI_LAUNCHER: launcher,
      },
      stdio: host ? ["pipe", "pipe", "pipe", "ipc"] : ["ignore", "inherit", "inherit", "ipc"],
      windowsHide: true,
    });
    note(next);
    if (host) {
      process.stdin.unpipe();
      process.stdin.pipe(next.stdin);
      next.stdout.pipe(process.stdout);
      next.stderr.pipe(process.stderr);
    }
    try {
      await waitFor(next, "ready", 30_000);
      process.exit(0);
    } catch (error) {
      next.kill("SIGTERM");
      const text = report(name, error, job);
      try {
        current = launch({ dir: state.currentDir, revision: state.current, replaceError: text });
        await waitHealthy(current);
      } catch (rollbackError) {
        report(name, `${text}; rollback failed: ${rollbackError instanceof Error ? rollbackError.message : rollbackError}`, job);
        process.exit(1);
      }
      replaced = false;
    }
  }

  try {
    current = launch({ dir: state.currentDir, revision: state.current });
  } catch (error) {
    report(name, error);
    process.exit(1);
  }
  if (process.env.MAGI_HEALTH === "1") {
    const code = await new Promise((resolve) => current.once("exit", (exit) => resolve(exit ?? 1)));
    process.exit(code);
  }
  try {
    await waitHealthy(current);
  } catch (error) {
    report(name, error);
    process.exit(1);
  }
  acceptingCrashes = true;
  if (typeof process.send === "function") process.send({ v: LAUNCHER_PROTOCOL, type: "ready" });
  const stop = () => {
    replaced = true;
    stopChild(current);
    process.exit(0);
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
