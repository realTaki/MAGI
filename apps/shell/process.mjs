/**
 * One OS-process supervisor. Every child is `node <entry> ...args` with cwd and env.
 *
 * @typedef {{ entry: string, cwd: string, args?: string[], env?: NodeJS.ProcessEnv }} ProcessSpec
 * @typedef {{ id: string, stop: () => void, onExit: (cb: (code: number | null, signal: string | null) => void) => void }} ProcessHandle
 * @typedef {(spec: ProcessSpec) => Promise<ProcessHandle>} StartProcess
 */

import { describeApp } from "./app-spec.mjs";
import { dependencyState } from "./dependency-state.mjs";
import { createProcessOwner } from "./process-owner.mjs";
import { ensureInstalledDependencies } from "./installation.mjs";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { basename, resolve } from "node:path";
import { createInterface } from "node:readline";

/** Bootstrap Shell before it can run its own prepare method. */
export async function prepareShellProcess(spec, { node, npm, env, managed, command, assertOpen = () => {} }) {
  assertOpen();
  const checkout = resolve(spec.cwd, "..", "..");
  if (managed) await ensureInstalledDependencies(checkout, { node, npm, env, command, assertOpen });
  assertOpen();
  if (!existsSync(spec.entry)) {
    await command(node, [npm, "run", "build:process"], {
      cwd: spec.cwd,
      env,
      description: "Could not build the Shell process",
    });
  }
  assertOpen();
  if (!existsSync(spec.entry)) {
    throw new Error(`The Shell process is missing: ${spec.entry}`);
  }
}

export async function prepareAndStartShell(spec, tools, supervisor, options) {
  supervisor.assertOpen();
  await prepareShellProcess(spec, { ...tools, assertOpen: () => supervisor.assertOpen() });
  supervisor.assertOpen();
  return await supervisor.start(spec, options);
}

export function createSupervisor({ node, env, spawnProcess = spawn, owner = createProcessOwner({ spawnProcess }), readyTimeoutMs = 30_000, stopTimeoutMs = 30_000 }) {
  const children = new Map();
  const records = new Map();
  const restarting = new Map();
  let seq = 0;
  let hostSend = null;
  let rebuildHandler = null;
  let closing = false, closePromise = null;
  const assertOpen = () => { if (closing) throw new Error("Shell supervisor is closing"); owner.assertOpen(); };

  function write(stream, message) {
    stream.write(`${JSON.stringify(message)}\n`);
  }

  function notifyHost(message) {
    if (hostSend === null) return;
    try { hostSend(message); }
    catch { /* Shell may be retiring; a replacement restores the current snapshot. */ }
  }

  function stop(id) {
    const entry = children.get(id);
    if (entry === undefined) return;
    entry.stopping = true;
    const { child } = entry;
    if (entry.graceful && child.connected) {
      child.send({ type: "shutdown" }, () => {});
      return;
    }
    entry.stopTask = owner.signal(child);
    void entry.stopTask.catch((error) => console.error("App tree stop failed:", error.message));
  }

  async function start(spec, { host, id: existingId, replacing = false } = {}) {
    assertOpen();
    const app = describeApp(spec);
    const identity = app?.key;
    const key = JSON.stringify([resolve(spec.cwd), resolve(spec.cwd, spec.entry), spec.args ?? []]);
    if (!existingId) {
      for (const [id, record] of records) {
        if (record.identity === identity && identity && restarting.has(id)) {
          if (record.key !== key) throw new Error(`Conflicting process specification: ${identity}`);
          const handle = await restarting.get(id);
          return { id: handle.id, whenReady: handle.whenReady };
        }
      }
      for (const [id, entry] of children) {
        if ((identity && entry.identity === identity) || entry.key === key) {
          if (entry.key !== key) throw new Error(`Conflicting process specification: ${identity}`);
          if (entry.stopping) {
            await new Promise((resolve, reject) => {
              const done = () => { clearTimeout(timer); resolve(); };
              const timer = setTimeout(() => {
                entry.child.removeListener("exit", done);
                reject(new Error(`App did not stop: ${spec.cwd}`));
              }, stopTimeoutMs);
              entry.child.once("exit", done);
            });
            return await start(spec, { host });
          }
          if (entry.host) throw new Error("Shell is already supervised");
          return { id, whenReady: entry.whenReady };
        }
      }
    }
    const id = existingId ?? String(++seq);
    const isHost = host !== undefined;
    const dependencies = app ? dependencyState(app.root) : null;
    assertOpen();
    const child = owner.spawn(node, [spec.entry, ...(spec.args ?? [])], {
      cwd: spec.cwd,
      env: { ...env, ...spec.env, ...(describeApp(spec)?.kind === "eva" ? { MAGI_SHELL_REBUILD: "ipc" } : {}) },
      stdio: isHost ? ["pipe", "pipe", "pipe", "ipc"] : ["ignore", "pipe", "pipe", "ipc"],
      detached: !isHost,
      windowsHide: true,
    });
    const entry = { child, host: isHost, key, identity, replacing, ready: false };
    children.set(id, entry);
    records.set(id, { spec, host, identity, key, dependencies, exit: null });
    const ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`App did not become ready: ${spec.cwd}`)), readyTimeoutMs);
      child.on("message", (message) => {
        if (message?.type === "runtime.rebuild") {
          let error;
          try {
            const app = describeApp(spec);
            if (children.get(id) !== entry || !entry.ready || entry.stopping || entry.replacing || app?.kind !== "eva") throw new Error("Only a live Shell-owned EVA may request a rebuild");
            if (Object.keys(message).sort().join(",") !== "id,target,type" || typeof message.id !== "string" || message.id.length > 64 || !["self", "app", "asp"].includes(message.target)) throw new Error("Invalid lifecycle request");
            if (!rebuildHandler) throw new Error("Shell lifecycle is not loaded");
            rebuildHandler({ target: message.target, handle: app.handle });
          } catch (failure) { error = failure instanceof Error ? failure.message : String(failure); }
          if (child.connected) child.send({ type: "runtime.rebuild.accepted", id: message.id, ...(error ? { error } : {}) }, () => {});
          return;
        }
        if (message?.type !== "ready") return;
        entry.graceful = describeApp(spec)?.kind === "eva" && Array.isArray(message.lifecycle) && message.lifecycle.includes("shutdown");
        entry.ready = true;
        clearTimeout(timer);
        resolve();
      });
      const fail = (error) => { clearTimeout(timer); reject(error); };
      child.once("error", (error) => {
        children.delete(id);
        fail(error);
      });
      child.once("exit", (code, signal) => fail(new Error(`App exited before ready (${code ?? signal})`)));
    });
    void ready.catch(() => undefined);
    entry.whenReady = () => ready;
    child.stderr?.on("data", (chunk) => {
      const text = String(chunk).trim();
      if (text) console.error(`[${basename(spec.entry)}]`, text);
    });
    child.once("exit", (code, signal) => {
      records.get(id).exit = { code, signal };
      children.delete(id);
      if (!isHost && !entry.replacing && hostSend !== null) {
        notifyHost({ type: "event", event: "process.exit", payload: { id, code, signal } });
      }
    });
    await new Promise((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });
    if (!isHost) return { id, whenReady: () => ready };

    const pending = new Map();
    let rpc = 0;
    const listeners = new Map();
    let exited = false;
    const send = (message) => {
      if (exited || child.stdin.destroyed) throw new Error("Shell process exited");
      write(child.stdin, message);
    };
    child.stdin.on("error", (error) => {
      for (const waiter of pending.values()) waiter.reject(error);
      pending.clear();
    });
    hostSend = send;

    const lines = createInterface({ input: child.stdout });
    lines.on("line", (line) => {
      if (line === "") return;
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        console.error(`[shell] bad ndjson: ${line.slice(0, 200)}`);
        return;
      }
      if (message?.type === "res") {
        pending.get(message.id)?.resolve(message.result);
        pending.delete(message.id);
        return;
      }
      if (message?.type === "err") {
        pending.get(message.id)?.reject(new Error(String(message.error)));
        pending.delete(message.id);
        return;
      }
      if (message?.type === "event") {
        for (const cb of listeners.get(message.event) ?? []) cb(message.payload);
        return;
      }
      if (message?.type !== "req") return;
      void (async () => {
        try {
          let result;
          if (message.method === "spawn") result = await start(message.params);
          else if (message.method === "processes") result = snapshots();
          else if (message.method === "stop") {
            stop(message.params?.id);
            result = {};
          } else if (typeof host[message.method] === "function") {
            result = await host[message.method](message.params);
          } else {
            throw new Error(`Unknown host method: ${String(message.method)}`);
          }
          if (!exited && !child.stdin.destroyed) send({ type: "res", id: message.id, result });
        } catch (error) {
          if (!exited && !child.stdin.destroyed) send({
            type: "err",
            id: message.id,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      })();
    });

    child.once("exit", () => {
      exited = true;
      lines.close();
      if (hostSend === send) hostSend = null;
      for (const waiter of pending.values()) waiter.reject(new Error("Shell process exited"));
      pending.clear();
    });

    return {
      id,
      whenReady: () => ready,
      invoke(method, params) {
        if (exited || child.stdin.destroyed) return Promise.reject(new Error("Shell process exited"));
        const reqId = String(++rpc);
        return new Promise((resolve, reject) => {
          pending.set(reqId, { resolve, reject });
          send({ type: "req", id: reqId, method, params });
        });
      },
      on(event, cb) {
        const list = listeners.get(event) ?? [];
        list.push(cb);
        listeners.set(event, list);
      },
      stop() {
        stop(id);
      },
    };
  }

  function snapshots() {
    return [...children]
      .map(([id]) => ({ id, spec: records.get(id).spec }));
  }

  async function replace(id) {
    assertOpen();
    const record = records.get(id);
    if (!record) throw new Error(`Unknown app process: ${id}`);
    const entry = children.get(id);
    if (entry) {
      entry.replacing = true;
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          entry.replacing = false;
          reject(new Error(`App did not stop: ${record.spec.cwd}`));
        }, stopTimeoutMs);
        entry.child.once("exit", () => { clearTimeout(timer); resolve(); });
        stop(id);
      });
      await entry.stopTask;
      await owner.reap(entry.child);
    }
    assertOpen();
    try {
      const handle = await start(record.spec, { host: record.host, id, replacing: true });
      await handle.whenReady();
      const restarted = children.get(id);
      if (!restarted) throw new Error("App exited after announcing ready");
      restarted.replacing = false;
      return handle;
    } catch (error) {
      const failed = children.get(id);
      if (failed) { failed.replacing = false; stop(id); }
      else if (hostSend) notifyHost({ type: "event", event: "process.exit", payload: { id, ...(records.get(id).exit ?? { code: null, signal: null }) } });
      throw error;
    }
  }

  function restart(id) {
    if (restarting.has(id)) return restarting.get(id);
    const operation = replace(id).finally(() => restarting.delete(id));
    restarting.set(id, operation);
    return operation;
  }

  return { start, stop, restart, snapshots,
    assertOpen,
    beginClose: () => { closing = true; owner.beginClose(); },
    close: () => closePromise ??= (async () => { closing = true; owner.beginClose(); for (const id of children.keys()) stop(id); await owner.close(); })(),
    setRebuildHandler: (handler) => { rebuildHandler = handler; },
    dependencyState: (id) => records.get(id)?.dependencies ?? null,
    stopAll: () => { for (const id of children.keys()) stop(id); },
  };
}
