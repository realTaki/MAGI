/**
 * One OS-process supervisor. Every child is `node <entry> ...args` with cwd and env.
 *
 * @typedef {{ entry: string, cwd: string, args?: string[], env?: NodeJS.ProcessEnv }} ProcessSpec
 * @typedef {{ id: string, stop: () => void, onExit: (cb: (code: number | null, signal: string | null) => void) => void }} ProcessHandle
 * @typedef {(spec: ProcessSpec) => Promise<ProcessHandle>} StartProcess
 */

import { describeApp } from "./app-spec.mjs";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { basename, resolve } from "node:path";
import { createInterface } from "node:readline";

/** Bootstrap User before it can run its own prepare method. */
export async function prepareUserProcess(spec, { node, npm, env, managed, command }) {
  const checkout = resolve(spec.cwd, "..", "..");
  if (managed && !existsSync(resolve(checkout, "node_modules"))) {
    if (!existsSync(resolve(checkout, "package-lock.json"))) {
      throw new Error(`MAGI checkout is incomplete: ${checkout}/package-lock.json is missing`);
    }
    await command(node, [npm, "ci", "--ignore-scripts"], {
      cwd: checkout,
      env,
      description: "Could not install the User process dependencies",
    });
  }
  if (!existsSync(spec.entry)) {
    await command(node, [npm, "run", "build:process"], {
      cwd: spec.cwd,
      env,
      description: "Could not build the User process",
    });
  }
  if (!existsSync(spec.entry)) {
    throw new Error(`The User process is missing: ${spec.entry}`);
  }
}

export function createSupervisor({ node, env, readyTimeoutMs = 30_000, stopTimeoutMs = 30_000 }) {
  const children = new Map();
  const records = new Map();
  const restarting = new Map();
  let seq = 0;
  let hostSend = null;

  function write(stream, message) {
    stream.write(`${JSON.stringify(message)}\n`);
  }

  function stop(id) {
    const entry = children.get(id);
    if (entry === undefined) return;
    const { child, host } = entry;
    if (!host && process.platform !== "win32" && typeof child.pid === "number" && child.pid > 0) {
      try {
        process.kill(-child.pid, "SIGTERM");
        return;
      } catch {
        // The group is already gone.
      }
    }
    child.kill("SIGTERM");
  }

  async function start(spec, { host, id: existingId, replacing = false } = {}) {
    const identity = describeApp(spec)?.key;
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
          if (entry.host) throw new Error("User is already supervised");
          return { id, whenReady: entry.whenReady };
        }
      }
    }
    const id = existingId ?? String(++seq);
    const isHost = host !== undefined;
    const child = spawn(node, [spec.entry, ...(spec.args ?? [])], {
      cwd: spec.cwd,
      env: { ...env, ...spec.env },
      stdio: isHost ? ["pipe", "pipe", "pipe", "ipc"] : ["ignore", "pipe", "pipe", "ipc"],
      detached: !isHost,
      windowsHide: true,
    });
    const entry = { child, host: isHost, key, identity, replacing, ready: false };
    children.set(id, entry);
    records.set(id, { spec, host, identity, key, exit: null });
    const ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`App did not become ready: ${spec.cwd}`)), readyTimeoutMs);
      child.on("message", (message) => {
        if (message?.type !== "ready") return;
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
        hostSend({ type: "event", event: "process.exit", payload: { id, code, signal } });
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
      if (exited || child.stdin.destroyed) throw new Error("User process exited");
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
        console.error(`[user] bad ndjson: ${line.slice(0, 200)}`);
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
          if (!exited) send({ type: "res", id: message.id, result });
        } catch (error) {
          if (!exited) send({
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
      for (const waiter of pending.values()) waiter.reject(new Error("User process exited"));
      pending.clear();
    });

    return {
      id,
      whenReady: () => ready,
      invoke(method, params) {
        if (exited) return Promise.reject(new Error("User process exited"));
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
    }
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
      else if (hostSend) hostSend({ type: "event", event: "process.exit", payload: { id, ...(records.get(id).exit ?? { code: null, signal: null }) } });
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
    stopAll: () => { for (const id of children.keys()) stop(id); },
  };
}
