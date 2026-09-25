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

  start(command: string, cwd: string): ShellState {
    const id = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
    const [file, ...args] = shellInvocation(command);
    const process = spawn(file, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    const state: ShellState = { id, command, process, output: "", cursor: 0, exitCode: null, status: "running" };
    this.shells.set(id, state);
    void this.capture(state, process.stdout);
    void this.capture(state, process.stderr, "[stderr]\n");
    process.once("close", (code) => {
      state.exitCode = code;
      if (state.status === "running") state.status = "completed";
    });
    return state;
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
      state.process.kill("SIGTERM");
      await Promise.race([new Promise<void>((resolve) => state.process.once("close", () => resolve())), sleep(5_000)]);
      if (state.exitCode === null) state.process.kill("SIGKILL");
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
