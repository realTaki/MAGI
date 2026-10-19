import { spawn, type ChildProcess } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

type ShellState = {
  id: string;
  command: string;
  process: ChildProcess;
  output: string;
  cursor: number;
  exitCode: number | null;
  status: "running" | "completed" | "killed";
};

export class ShellManager {
  private readonly shells = new Map<string, ShellState>();

  start(command: string, cwd: string, env: NodeJS.ProcessEnv = process.env): ShellState {
    const id = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
    const [file, ...args] = shellInvocation(command);
    const child = spawn(file, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"], detached: process.platform !== "win32", windowsHide: true });
    const state: ShellState = { id, command, process: child, output: "", cursor: 0, exitCode: null, status: "running" };
    this.shells.set(id, state);
    void this.capture(state, child.stdout);
    void this.capture(state, child.stderr, "[stderr]\n");
    child.once("close", (code) => {
      state.exitCode = code;
      if (state.status === "running") state.status = "completed";
    });
    return state;
  }

  async foreground(command: string, cwd: string, timeout: number, signal?: AbortSignal, env: NodeJS.ProcessEnv = process.env): Promise<string> {
    signal?.throwIfAborted();
    const state = this.start(command, cwd, env);
    let timer: ReturnType<typeof setTimeout> | undefined;
    let aborted: (() => void) | undefined;
    try {
      await new Promise<void>((resolve, reject) => {
        state.process.once("close", () => resolve());
        state.process.once("error", reject);
        timer = setTimeout(() => reject(new Error("Command timed out")), timeout);
        if (signal) {
          aborted = () => reject(signal.reason);
          signal.addEventListener("abort", aborted, { once: true });
        }
      });
      return `exit_code: ${state.exitCode ?? "error"}\n${state.output}`.slice(0, 8192);
    } finally {
      if (timer) clearTimeout(timer);
      if (signal && aborted) signal.removeEventListener("abort", aborted);
      await this.kill(state.id);
    }
  }

  read(id: string, filter?: string): string {
    const state = this.shells.get(id);
    if (!state) throw new Error(`shell not found: ${id}; available: ${this.list().join(", ") || "none"}`);
    const fresh = state.output.slice(state.cursor);
    state.cursor = state.output.length;
    let selected = fresh;
    if (filter) {
      try { const pattern = new RegExp(filter); selected = fresh.split(/\r?\n/).filter((line) => pattern.test(line)).join("\n"); }
      catch { /* invalid regex means no filter */ }
    }
    const exit = state.exitCode === null ? "" : ` exit=${state.exitCode}`;
    return `${selected || "(no new output)"}\n[status] ${state.status}${exit}`;
  }

  async kill(id: string): Promise<string> {
    const state = this.shells.get(id);
    if (!state) return `Shell already gone: ${id}.`;
    const tail = state.output.slice(state.cursor);
    if (state.exitCode === null) {
      state.status = "killed";
      const child = state.process;
      const signalTree = (signal: NodeJS.Signals) => {
        if (process.platform === "win32" && child.pid) {
          const killer = spawn("taskkill.exe", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore", windowsHide: true });
          killer.on("error", () => child.kill(signal));
        } else {
          try { if (child.pid) process.kill(-child.pid, signal); else child.kill(signal); }
          catch { child.kill(signal); }
        }
      };
      signalTree("SIGTERM");
      await Promise.race([new Promise<void>((resolve) => child.once("close", () => resolve())), sleep(1000)]);
      if (child.exitCode === null && child.signalCode === null) signalTree("SIGKILL");
    }
    this.shells.delete(id);
    return `${tail ? `Last output before kill:\n${tail.trimEnd()}\n` : ""}Killed.`;
  }

  list(): string[] { return [...this.shells.keys()]; }

  async shutdown(): Promise<void> {
    await Promise.all(this.list().map((id) => this.kill(id)));
  }

  private async capture(state: ShellState, stream: NodeJS.ReadableStream | null | undefined, prefix = ""): Promise<void> {
    if (!stream) return;
    let first = true;
    try {
      for await (const chunk of stream) {
        state.output += `${first ? prefix : ""}${String(chunk)}`;
        first = false;
        if (state.output.length > 256 * 1024) {
          const removed = state.output.length - 256 * 1024;
          state.output = state.output.slice(removed);
          state.cursor = Math.max(0, state.cursor - removed);
        }
      }
    } catch {
      // A killed child can close its pipes with an error after its useful output.
    }
  }
}

export function shellInvocation(command: string): string[] {
  return process.platform === "win32"
    ? ["powershell.exe", "-NoProfile", "-Command", command]
    : ["bash", "-lc", command];
}
