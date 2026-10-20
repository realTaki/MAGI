import { LAUNCHER_PROTOCOL } from "@magi/launcher/runtime/protocol.js";
import { spawn, type ChildProcess } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

type ShellState = {
  id: string;
  command: string;
  process: ChildProcess;
  output: string;
  cursor: number;
  exitCode: number | null;
  closing?: Promise<string>;
  status: "running" | "completed" | "killed";
};

export class ShellManager {
  private readonly shells = new Map<string, ShellState>();

  start(command: string, cwd: string, env: NodeJS.ProcessEnv = process.env): ShellState {
    const id = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
    const supervisor = process.env.MAGI_TOOL_PROCESS_SUPERVISOR_PID;
    // A pre-marker launcher can still acknowledge ownership during an in-place
    // replacement; retain that upgrade path without treating arbitrary IPC as it.
    const knownLauncher = supervisor === String(process.ppid) || (!supervisor && Boolean(process.env.MAGI_LAUNCHER));
    if (process.connected && !knownLauncher) {
      throw new Error("Cannot start bash: IPC parent is not the MAGI launcher and cannot own tool processes. Start EVA through its launcher.");
    }
    const supervised = process.connected && process.platform !== "win32";
    // Do not execute user code until the launcher owns this process group. If the
    // worker dies before registration, the idle gate expires without side effects.
    const [file, ...args] = supervised
      ? ["bash", "-c", 'if ! read -r -t 5 ready; then echo "MAGI launcher did not acknowledge tool-process ownership; command was not started" >&2; exit 125; fi; exec bash -lc "$1"', "--", command]
      : shellInvocation(command);
    const child = spawn(file, args, { cwd, env, stdio: [supervised ? "pipe" : "ignore", "pipe", "pipe"], detached: process.platform !== "win32", windowsHide: true });
    const state: ShellState = { id, command, process: child, output: "", cursor: 0, exitCode: null, status: "running" };
    this.shells.set(id, state);
    // The live launcher owns detached groups if this worker is killed abruptly.
    if (child.pid && process.connected) {
      const ready = (message: unknown) => {
        const ack = message as { v?: number; type?: string; pid?: number };
        if (ack?.v !== LAUNCHER_PROTOCOL || ack.type !== "tool-process-ready" || ack.pid !== child.pid) return;
        process.off("message", ready);
        child.stdin?.end("ready\n");
      };
      if (supervised) {
        process.on("message", ready);
        child.once("close", () => process.off("message", ready));
        child.stdin?.on("error", () => {});
      }
      process.send?.({ v: LAUNCHER_PROTOCOL, type: "tool-process", action: "add", pid: child.pid }, () => {});
    }
    void this.capture(state, child.stdout);
    void this.capture(state, child.stderr, "[stderr]\n");
    child.once("close", (code) => {
      state.exitCode = code;
      if (state.status === "running") state.status = "completed";
      // Retire ownership promptly on natural completion, preserving output for reads.
      // Reap any remaining group members before removing the launcher's PID entry.
      if (!state.closing) state.closing = this.reap(state, false);
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
      return clipShellOutput(`exit_code: ${state.exitCode ?? "error"}\n${state.output}`);
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
    if (state.closing) { const result = await state.closing; this.shells.delete(id); return result; }
    state.closing = this.reap(state);
    return state.closing;
  }

  private async reap(state: ShellState, forget = true): Promise<string> {
    const tail = state.output.slice(state.cursor);
    const child = state.process;
    if (forget) state.status = "killed";
    const signalTree = async (signal: NodeJS.Signals) => {
      if (!child.pid) return;
      if (process.platform === "win32") {
        await new Promise<void>((resolve) => {
          const killer = spawn("taskkill.exe", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore", windowsHide: true });
          killer.once("error", () => { child.kill(signal); resolve(); });
          killer.once("close", () => resolve());
        });
      } else {
        try { process.kill(-child.pid, signal); } catch { /* group already gone */ }
      }
    };
    const closed = child.exitCode !== null || child.signalCode !== null
      ? Promise.resolve() : new Promise<void>((resolve) => child.once("close", () => resolve()));
    await signalTree("SIGTERM");
    // Always escalate the group: its leader can exit while a descendant ignores TERM.
    await Promise.race([closed, sleep(250)]);
    await signalTree("SIGKILL");
    await Promise.race([closed, sleep(1000)]);
    if (child.pid && process.connected) process.send?.({ v: LAUNCHER_PROTOCOL, type: "tool-process", action: "remove", pid: child.pid }, () => {});
    if (forget) this.shells.delete(state.id);
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

// Encode the script so Windows argv quoting cannot rewrite PowerShell string literals.
/** Keep a head and tail so long compile/test failure summaries stay visible. */
export function clipShellOutput(text: string, limit = 8192): string {
  if (text.length <= limit) return text;
  const marker = "\n…\n";
  const keep = Math.floor((limit - marker.length) / 2);
  return `${text.slice(0, keep)}${marker}${text.slice(-keep)}`;
}

export function shellInvocation(command: string): string[] {
  return process.platform === "win32"
    ? ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(command, "utf16le").toString("base64")]
    : ["bash", "-lc", command];
}
