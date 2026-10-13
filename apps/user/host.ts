import { createInterface } from "node:readline";
import type { Readable, Writable } from "node:stream";

export type ProcessSpec = {
  entry: string;
  cwd: string;
  args?: string[];
  env?: NodeJS.ProcessEnv;
};

export type ProcessHandle = {
  id: string;
  stop(): void;
  onExit(cb: (code: number | null, signal: string | null) => void): void;
};

export type StartProcess = (spec: ProcessSpec) => Promise<ProcessHandle>;

type Req = { type: "req"; id: string; method: string; params?: unknown };

function send(stdout: Writable, message: object): void {
  stdout.write(`${JSON.stringify(message)}\n`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

export function connectHost(stdin: Readable, stdout: Writable) {
  let seq = 0;
  const pending = new Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();
  const exitWaiters = new Map<string, Array<(code: number | null, signal: string | null) => void>>();
  const exits = new Map<string, { code: number | null; signal: string | null }>();
  const queued: Req[] = [];
  let handlers: Record<string, (params: unknown) => unknown> | null = null;

  function rpc(method: string, params?: unknown): Promise<unknown> {
    const id = String(++seq);
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      send(stdout, { type: "req", id, method, params });
    });
  }

  async function dispatch(req: Req): Promise<void> {
    const handler = handlers?.[req.method];
    if (handler === undefined) {
      send(stdout, { type: "err", id: req.id, error: `Unknown app method: ${req.method}` });
      return;
    }
    try {
      const result = await handler(req.params);
      send(stdout, { type: "res", id: req.id, result });
    } catch (error) {
      send(stdout, {
        type: "err",
        id: req.id,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  function deliverExit(id: string, code: number | null, signal: string | null): void {
    const waiters = exitWaiters.get(id);
    if (waiters !== undefined) {
      for (const cb of waiters) cb(code, signal);
      exitWaiters.delete(id);
      return;
    }
    exits.set(id, { code, signal });
  }

  const lines = createInterface({ input: stdin });
  lines.on("line", (line) => {
    if (line === "") return;
    let message: unknown;
    try {
      message = JSON.parse(line);
    } catch {
      return;
    }
    if (!isRecord(message) || typeof message.type !== "string") return;
    const rec = message;
    if (rec.type === "res" && typeof rec.id === "string") {
      pending.get(rec.id)?.resolve(rec.result);
      pending.delete(rec.id);
      return;
    }
    if (rec.type === "err" && typeof rec.id === "string") {
      pending.get(rec.id)?.reject(new Error(typeof rec.error === "string" ? rec.error : "host error"));
      pending.delete(rec.id);
      return;
    }
    if (rec.type === "event" && rec.event === "process.exit") {
      const payload = isRecord(rec.payload) ? rec.payload : null;
      if (payload === null || typeof payload.id !== "string") return;
      const code = payload.code === null || typeof payload.code === "number" ? payload.code : null;
      const signal = payload.signal === null || typeof payload.signal === "string" ? payload.signal : null;
      deliverExit(payload.id, code, signal);
      return;
    }
    if (rec.type === "req" && typeof rec.id === "string" && typeof rec.method === "string") {
      const req: Req = { type: "req", id: rec.id, method: rec.method, params: rec.params };
      if (handlers === null) queued.push(req);
      else void dispatch(req);
    }
  });

  async function startProcess(spec: ProcessSpec): Promise<ProcessHandle> {
    const spawned = await rpc("spawn", spec);
    const result = isRecord(spawned) ? spawned : null;
    if (result === null || typeof result.id !== "string") throw new Error("spawn did not return an id");
    return processHandle(result.id);
  }

  function processHandle(id: string): ProcessHandle {
    return {
      id,
      stop() {
        void rpc("stop", { id });
      },
      onExit(cb) {
        const done = exits.get(id);
        if (done !== undefined) {
          exits.delete(id);
          cb(done.code, done.signal);
          return;
        }
        const list = exitWaiters.get(id) ?? [];
        list.push(cb);
        exitWaiters.set(id, list);
      },
    };
  }

  return {
    spawn: (spec: ProcessSpec) => rpc("spawn", spec),
    stop: (params: { id: string }) => rpc("stop", params),
    openExternal: (params: { url: string }) => rpc("openExternal", params),
    copy: (params: { text: string }) => rpc("copy", params),
    installShellUpdate: (params: { installer: string }) => rpc("installShellUpdate", params),
    emit: (params: { event: string; payload?: unknown }) => rpc("emit", params),
    progress: (params: { message: string; percent: number }) => rpc("progress", params),
    command: (params: { file: string; args: string[]; options: { cwd: string; env?: NodeJS.ProcessEnv; description?: string } }) => rpc("command", params) as Promise<string>,
    prepareDependencies: (root: string) => rpc("prepareDependencies", { root }),
    startProcess,
    async restoreProcesses(): Promise<Array<{ spec: ProcessSpec; handle: ProcessHandle }>> {
      const result = await rpc("processes");
      if (!Array.isArray(result)) throw new Error("processes did not return a list");
      return result.filter((entry) => entry.spec?.entry && !entry.spec.entry.endsWith("user.js"))
        .map((entry) => ({ spec: entry.spec as ProcessSpec, handle: processHandle(entry.id as string) }));
    },
    close() {
      lines.close();
      stdin.destroy();
      for (const waiter of pending.values()) waiter.reject(new Error("Host connection closed"));
      pending.clear();
    },
    serve(next: Record<string, (params: unknown) => unknown>) {
      handlers = next;
      for (const req of queued.splice(0)) void dispatch(req);
    },
  };
}
