/**
 * eva is the launcher. It is not a BUS. One child process is the node.
 * Shell, ASP, and a cloud agent differ only by --role.
 */

import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { magiHome } from "@magi/runtime/home.js";
import { LAUNCHER_PROTOCOL } from "@magi/runtime/protocol.js";
import { resolveToolchain } from "@magi/runtime/toolchain.js";

const childScript = process.env.MAGI_CHILD
  || fileURLToPath(new URL("../../packages/node/dist/child.js", import.meta.url));

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
  try {
    const body = JSON.parse(readFileSync(statePath(name), "utf8"));
    if (body?.protocol !== LAUNCHER_PROTOCOL) return { protocol: LAUNCHER_PROTOCOL, current: null, previous: null };
    return body;
  } catch {
    return { protocol: LAUNCHER_PROTOCOL, current: null, previous: null };
  }
}

function writeState(name, state) {
  const file = statePath(name);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, JSON.stringify({ protocol: LAUNCHER_PROTOCOL, current: state.current ?? null, previous: state.previous ?? null }));
}

function writeMarker(name, error, job = null) {
  const file = markerPath(name);
  mkdirSync(dirname(file), { recursive: true });
  const id = Number.isInteger(job) ? job : null;
  writeFileSync(file, JSON.stringify({ job: id, error }));
}

function spawnChild(role, rest, revision, replaceJob) {
  const { node } = resolveToolchain();
  const host = process.env.MAGI_HOST === "1";
  const env = {
    ...process.env,
    MAGI_ROLE: role,
    MAGI_HOME: magiHome(),
    ...(rest[0] ? { MAGI_HANDLE: rest[0] } : {}),
    ...(rest[1] ? { MAGI_ASP: rest[1] } : {}),
    ...(rest[2] ? { MAGI_ASP_TOKEN: rest[2] } : {}),
    ...(revision ? { MAGI_REVISION: revision } : {}),
  };
  delete env.MAGI_REPLACE_JOB;
  delete env.MAGI_REPLACE_ERROR;
  if (Number.isInteger(replaceJob)) env.MAGI_REPLACE_JOB = String(replaceJob);
  const child = spawn(node, [childScript, "--role", role, ...rest], {
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
  return child;
}

function waitReady(child, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("child did not become ready")), timeoutMs);
    const onExit = (code, signal) => {
      clearTimeout(timer);
      reject(new Error(`child exited ${code ?? signal ?? "null"}`));
    };
    const onMessage = (message) => {
      if (message?.type !== "ready") return;
      clearTimeout(timer);
      child.off("exit", onExit);
      resolve();
    };
    child.once("exit", onExit);
    child.on("message", onMessage);
  });
}

function stopChild(child) {
  if (child.exitCode != null || child.signalCode != null) return;
  if (child.connected) child.send({ v: LAUNCHER_PROTOCOL, type: "shutdown" });
  else child.kill("SIGTERM");
}

async function handoff(role, rest, name, revision) {
  const { node } = resolveToolchain();
  const next = spawn(node, [fileURLToPath(import.meta.url), "--role", role, ...rest], {
    env: { ...process.env, MAGI_HANDOFF: "1", MAGI_REVISION: revision, MAGI_HOME: magiHome() },
    stdio: ["ignore", "inherit", "inherit", "ipc"],
    windowsHide: true,
  });
  try {
    await waitReady(next, 30_000);
    return next;
  } catch (error) {
    next.kill("SIGTERM");
    writeMarker(name, error instanceof Error ? error.message : String(error));
    return null;
  }
}

async function main() {
  const { role, rest } = args();
  const name = nodeName(role, rest);
  const state = readState(name);
  if (process.env.MAGI_REVISION) state.current = process.env.MAGI_REVISION;
  let current = spawnChild(role, rest, state.current);
  const ready = waitReady(current, 30_000);
  if (process.env.MAGI_HEALTH === "1") {
    const code = await new Promise((resolve) => current.once("exit", (exit) => resolve(exit ?? 1)));
    process.exit(code);
  }
  try {
    await ready;
  } catch (error) {
    writeMarker(name, error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
  if (process.env.MAGI_HANDOFF === "1") process.send?.({ v: LAUNCHER_PROTOCOL, type: "ready" });
  else process.send?.({ type: "ready" });
  const stop = () => {
    stopChild(current);
    process.exit(0);
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);

  let replaced = false;
  const watch = (child) => {
    child.on("message", (message) => { void onMessage(message); });
    child.on("exit", () => {
      if (replaced || process.env.MAGI_HANDOFF === "1" || child !== current) return;
      current = spawnChild(role, rest, state.current);
      watch(current);
    });
  };
  const onMessage = async (message) => {
    if (message?.v !== LAUNCHER_PROTOCOL) return;
    if (message.type === "upgrade") {
      replaced = true;
      const replacement = await handoff(role, rest, name, String(message.revision ?? state.current ?? ""));
      if (!replacement) { replaced = false; return; }
      stopChild(current);
      process.exit(0);
    }
    if (message.type !== "switch") return;
    replaced = true;
    const revision = String(message.revision ?? "");
    const job = Number.isInteger(message.job) ? message.job : null;
    const next = spawnChild(role, rest, revision, job);
    try {
      await waitReady(next, 30_000);
      const previous = state.current;
      const old = current;
      current = next;
      watch(current);
      stopChild(old);
      state.previous = previous;
      state.current = revision;
      writeState(name, state);
    } catch (error) {
      next.kill("SIGTERM");
      const text = error instanceof Error ? error.message : String(error);
      writeMarker(name, text, job);
      if (current.connected) current.send({ v: LAUNCHER_PROTOCOL, type: "switch-failed", error: text, job });
      if (message.previous && message.checkout) {
        const { git } = resolveToolchain();
        spawn(git, ["checkout", String(message.previous)], { cwd: String(message.checkout), stdio: "ignore" });
      }
    } finally {
      replaced = false;
    }
  };
  watch(current);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
