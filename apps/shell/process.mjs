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
import { cpSync, existsSync, mkdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { ASP_MAGI_HANDLE, SHELL_MAGI_HANDLE } from "@magi/bus";
import { replaceFailedPath } from "@magi/bus/replaceMarker.js";
import { LAUNCHER_PROTOCOL } from "@magi/runtime/protocol.js";
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

export function createSupervisor({ node, env, spawnProcess = spawn, owner = createProcessOwner({ spawnProcess }), readyTimeoutMs = 30_000, stopTimeoutMs = 30_000, onHostRestart } = {}) {
  const children = new Map();
  const records = new Map();
  const restarting = new Map();
  const replaceJobs = new Map();
  const replaceErrors = new Map();
  let seq = 0;
  let hostSend = null;
  let rebuildHandler = null;
  let closing = false, closePromise = null;
  const assertOpen = () => { if (closing) throw new Error("Shell supervisor is closing"); owner.assertOpen(); };

  function write(stream, message) {
    stream.write(`${JSON.stringify(message)}\n`);
  }

  /** The bus handle for a supervised app. describeApp aliases asp and shell. */
  function markerHandle(spec) {
    const app = describeApp(spec);
    if (!app) return null;
    if (app.kind === "asp") return ASP_MAGI_HANDLE;
    if (app.kind === "shell") return SHELL_MAGI_HANDLE;
    return app.handle;
  }

  /**
   * The old process did not start. Leave the app database alone. Tell the
   * operator either way, and record the failure where the next start can fail that job.
   */
  function recordReplaceFailure(spec, jobId, text) {
    const payload = { error: text };
    const handle = markerHandle(spec);
    if (handle) {
      const file = replaceFailedPath(handle);
      const temporary = `${file}.${process.pid}.tmp`;
      try {
        mkdirSync(dirname(file), { recursive: true });
        const job = Number(jobId);
        writeFileSync(temporary, JSON.stringify({ job: Number.isInteger(job) ? job : null, error: text }));
        renameSync(temporary, file);
      } catch (error) {
        payload.writeError = error instanceof Error ? error.message : String(error);
        try { rmSync(temporary, { force: true }); }
        catch { /* The notice below is the record of this failure. */ }
      }
    }
    notifyHost({ type: "event", event: "process.replace", payload });
  }

  function restoreDist(spec) {
    const live = resolve(spec.cwd, "dist");
    const backup = `${live}.rollback`;
    if (!existsSync(backup)) return;
    rmSync(live, { recursive: true, force: true });
    cpSync(backup, live, { recursive: true });
    rmSync(backup, { recursive: true, force: true });
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
    const ownerHandle = spec.env?.MAGI_HANDLE;
    const ownerBridge = !isHost
      && basename(resolve(spec.cwd)) === "eva" && basename(dirname(resolve(spec.cwd))) === "apps"
      && resolve(spec.cwd, spec.entry) === resolve(spec.cwd, "eva.mjs")
      && spec.args?.length === 3 && spec.args[0] === "--role" && spec.args[1] === "agent" && spec.args[2] === ownerHandle
      && /^@[A-Za-z0-9][A-Za-z0-9._-]{0,62}\.magi$/.test(ownerHandle ?? "")
      && !["@shell.magi", "@asp.magi", "@user.magi"].includes(ownerHandle)
      && spec.env?.MAGI_HOST === "1" && spec.env?.MAGI_SERVER === "1" && spec.env?.MAGI_ROLE === "agent";
    const dependencies = app ? dependencyState(app.root) : null;
    assertOpen();
    const replaceJob = replaceJobs.get(id);
    const replaceError = replaceErrors.get(id);
    replaceJobs.delete(id);
    replaceErrors.delete(id);
    const child = owner.spawn(node, [spec.entry, ...(spec.args ?? [])], {
      cwd: spec.cwd,
      env: {
        ...env,
        ...spec.env,
        ...(replaceJob ? { MAGI_REPLACE_JOB: replaceJob } : {}),
        ...(replaceError ? { MAGI_REPLACE_ERROR: replaceError } : {}),
      },
      stdio: isHost || ownerBridge ? ["pipe", "pipe", "pipe", "ipc"] : ["ignore", "pipe", "pipe", "ipc"],
      detached: !isHost,
      windowsHide: true,
    });
    const entry = { child, host: isHost, ownerBridge, key, identity, replacing, ready: false, upgrade: null };
    children.set(id, entry);
    records.set(id, { spec, host, identity, key, dependencies, exit: null });
    let stderrText = "";
    const ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`App did not become ready: ${spec.cwd}`)), readyTimeoutMs);
      child.on("message", (message) => {
        if (message?.type === "runtime.replace") {
          const live = children.get(id) === entry && entry.ready && !entry.stopping && !entry.replacing && describeApp(spec);
          if (!live) {
            if (child.connected) child.send({ type: "runtime.replace.accepted", error: "replace was refused" }, () => {});
            return;
          }
          const job = typeof message.job === "number" ? String(message.job) : undefined;
          if (job) replaceJobs.set(id, job);
          if (child.connected) child.send({ type: "runtime.replace.accepted" }, () => {});
          void restart(id).catch((error) => {
            const text = error instanceof Error ? error.message : String(error);
            replaceJobs.delete(id);
            if (child.connected) {
              child.send({ type: "runtime.replace.accepted", error: text }, () => {});
              return;
            }
            const record = records.get(id);
            if (!record) return;
            restoreDist(record.spec);
            if (job) replaceJobs.set(id, job);
            replaceErrors.set(id, text);
            void start(record.spec, { host: record.host, id, replacing: true }).then((handle) => handle.whenReady()).catch((startError) => {
              const detail = startError instanceof Error ? startError.message : String(startError);
              replaceJobs.delete(id);
              replaceErrors.delete(id);
              recordReplaceFailure(record.spec, job, `${text}; ${detail}`);
            });
          });
          return;
        }
        if (message?.type === "interface-updated" && isHost && entry.ready && !entry.stopping && message.v === LAUNCHER_PROTOCOL && typeof message.ui === "string") {
          void host.emit?.({ event: "app.interface-updated", payload: { ui: message.ui } });
          return;
        }
        if (message?.type === "failed") {
          const error = typeof message.error === "string" && message.error !== "" ? message.error : "launcher failed";
          notifyHost({ type: "event", event: "launcher.failed", payload: { error } });
          return;
        }
        if (message?.type === "upgrade" && isHost && message.v === LAUNCHER_PROTOCOL && typeof message.launcher === "string") {
          entry.upgrade = {
            entry: message.launcher,
            cwd: dirname(message.launcher),
            args: spec.args,
            env: {
              ...spec.env,
              MAGI_HOST: "1",
              MAGI_LAUNCHER: message.launcher,
              MAGI_REVISION: String(message.revision ?? ""),
              MAGI_REVISION_DIR: String(message.dir ?? ""),
              ...(Number.isInteger(message.job) ? { MAGI_REPLACE_JOB: String(message.job) } : {}),
            },
          };
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
        if (children.get(id)?.child === child) children.delete(id);
        fail(error);
      });
      child.stderr?.on("data", (chunk) => { stderrText = `${stderrText}${chunk}`.slice(-2000); });
      child.once("exit", (code, signal) => {
        if (entry.upgrade) return;
        fail(new Error(stderrText.trim() || `App exited before ready (${code ?? signal})`));
      });
    });
    void ready.catch(() => undefined);
    entry.whenReady = () => ready;
    child.stderr?.on("data", (chunk) => {
      const text = String(chunk).trim();
      if (text) console.error(`[${basename(spec.entry)}]`, text);
    });
    child.once("exit", (code, signal) => {
      if (children.get(id)?.child !== child) return;
      const upgrade = entry.upgrade;
      records.get(id).exit = { code, signal };
      children.delete(id);
      if (isHost && upgrade && !entry.stopping) {
        const next = { ...spec, entry: upgrade.entry, cwd: upgrade.cwd, args: upgrade.args, env: upgrade.env };
        records.set(id, { ...records.get(id), spec: next });
        const previousSpec = spec;
        void start(next, { host, id, replacing: true }).then(async (handle) => {
          await handle.whenReady();
          onHostRestart?.(handle);
        }).catch(async (error) => {
          const text = error instanceof Error ? error.message : String(error);
          notifyHost({ type: "event", event: "launcher.failed", payload: { error: text } });
          records.set(id, { ...records.get(id), spec: previousSpec });
          try {
            const restored = await start(previousSpec, { host, id, replacing: true });
            await restored.whenReady();
            onHostRestart?.(restored);
          } catch (rollbackError) {
            const detail = rollbackError instanceof Error ? rollbackError.message : String(rollbackError);
            notifyHost({ type: "event", event: "launcher.failed", payload: { error: `${text}; rollback failed: ${detail}` } });
          }
        });
        return;
      }
      if (isHost && entry.ready && code !== 0 && code !== null) {
        notifyHost({ type: "event", event: "launcher.failed", payload: { error: stderrText.trim() || `launcher exited ${code}` } });
      }
      if (!isHost && !entry.replacing && hostSend !== null) {
        notifyHost({ type: "event", event: "process.exit", payload: { id, code, signal } });
      }
    });
    await new Promise((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });
    if (!isHost && !ownerBridge) return { id, whenReady: () => ready };

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
    if (isHost) hostSend = send;

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
          if (!isHost) throw new Error("Managed EVA cannot invoke desktop host methods");
          if (message.method === "process.invoke") {
            const target = children.get(message.params?.id);
            if (!target?.ownerBridge || target.stopping || !target.ready || typeof target.invokeOwner !== "function") throw new Error("Owned EVA process is unavailable");
            if (!["owner.chat", "owner.events", "owner.profile"].includes(message.params?.method)) throw new Error("Owner method is not allowed");
            result = await target.invokeOwner(message.params.method, message.params.params);
          } else if (message.method === "spawn") result = await start(message.params);
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

    const handle = {
      id,
      whenReady: () => ready,
      invoke(method, params) {
        if (exited || child.stdin.destroyed) return Promise.reject(new Error("Shell process exited"));
        const reqId = String(++rpc);
        return new Promise((resolve, reject) => {
          const timer = ownerBridge ? setTimeout(() => {
            pending.delete(reqId);
            reject(new Error("Owned EVA request timed out"));
          }, readyTimeoutMs) : undefined;
          pending.set(reqId, {
            resolve: (value) => { clearTimeout(timer); resolve(value); },
            reject: (error) => { clearTimeout(timer); reject(error); },
          });
          try { send({ type: "req", id: reqId, method, params }); }
          catch (error) { pending.get(reqId)?.reject(error); pending.delete(reqId); }
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
    if (ownerBridge) entry.invokeOwner = (method, params) => handle.invoke(method, params);
    return handle;
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
    consumeDesktopHandoff: (journalPath) => {
      for (const record of records.values()) {
        const environment = record.spec.env;
        if (environment?.MAGI_DESKTOP_UPDATE_JOURNAL !== journalPath) continue;
        for (const key of Object.keys(environment)) if (key.startsWith("MAGI_DESKTOP_UPDATE_") || ["MAGI_REVISION", "MAGI_REVISION_DIR", "MAGI_REPLACE_JOB", "MAGI_REPLACE_ERROR"].includes(key)) delete environment[key];
      }
    },
    setRebuildHandler: (handler) => { rebuildHandler = handler; },
    dependencyState: (id) => records.get(id)?.dependencies ?? null,
    stopAll: () => { for (const id of children.keys()) stop(id); },
  };
}
