/**
 * One OS-process supervisor. Every child is `node <entry> ...args` with cwd and env.
 *
 * @typedef {{ entry: string, cwd: string, args?: string[], env?: NodeJS.ProcessEnv }} ProcessSpec
 * @typedef {{ id: string, stop: () => void, onExit: (cb: (code: number | null, signal: string | null) => void) => void }} ProcessHandle
 * @typedef {(spec: ProcessSpec) => Promise<ProcessHandle>} StartProcess
 */

import { spawn } from "node:child_process";
import { basename } from "node:path";
import { createInterface } from "node:readline";

export function createSupervisor({ node, env }) {
  const children = new Map();
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

  async function start(spec, { host } = {}) {
    const id = String(++seq);
    const isHost = host !== undefined;
    const child = spawn(node, [spec.entry, ...(spec.args ?? [])], {
      cwd: spec.cwd,
      env: { ...env, ...spec.env },
      stdio: isHost ? ["pipe", "pipe", "pipe"] : ["ignore", "pipe", "pipe"],
      detached: !isHost,
      windowsHide: true,
    });
    children.set(id, { child, host: isHost });
    child.stderr?.on("data", (chunk) => {
      const text = String(chunk).trim();
      if (text) console.error(`[${basename(spec.entry)}]`, text);
    });
    child.once("exit", (code, signal) => {
      children.delete(id);
      if (!isHost && hostSend !== null) {
        hostSend({ type: "event", event: "process.exit", payload: { id, code, signal } });
      }
    });
    await new Promise((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });
    if (!isHost) return { id };

    const pending = new Map();
    let rpc = 0;
    const listeners = new Map();
    const send = (message) => write(child.stdin, message);
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
          else if (message.method === "stop") {
            stop(message.params?.id);
            result = {};
          } else if (typeof host[message.method] === "function") {
            result = await host[message.method](message.params);
          } else {
            throw new Error(`Unknown host method: ${String(message.method)}`);
          }
          send({ type: "res", id: message.id, result });
        } catch (error) {
          send({
            type: "err",
            id: message.id,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      })();
    });

    child.once("exit", () => {
      lines.close();
      if (hostSend === send) hostSend = null;
      for (const waiter of pending.values()) waiter.reject(new Error("User process exited"));
      pending.clear();
    });

    return {
      id,
      invoke(method, params) {
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

  return { start, stop };
}
