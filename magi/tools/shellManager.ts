type ShellState = {
  id: string;
  command: string;
  process: ReturnType<typeof Bun.spawn>;
  output: string;
  cursor: number;
  exitCode: number | null;
  status: "running" | "completed" | "killed";
};

export class ShellManager {
  private readonly shells = new Map<string, ShellState>();

  start(command: string, cwd: string): ShellState {
    const id = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
    const process = Bun.spawn(shellInvocation(command), { cwd, stdout: "pipe", stderr: "pipe" });
    const state: ShellState = { id, command, process, output: "", cursor: 0, exitCode: null, status: "running" };
    this.shells.set(id, state);
    void this.capture(state, process.stdout);
    void this.capture(state, process.stderr, "[stderr]\n");
    void process.exited.then((code) => {
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
      await Promise.race([state.process.exited, Bun.sleep(5_000)]);
      if (state.exitCode === null) state.process.kill("SIGKILL");
    }
    this.shells.delete(id);
    return `${tail ? `Last output before kill:\n${tail.trimEnd()}\n` : ""}Killed.`;
  }

  list(): string[] { return [...this.shells.keys()]; }

  async shutdown(): Promise<void> {
    await Promise.all(this.list().map((id) => this.kill(id)));
  }

  private async capture(state: ShellState, stream: ReadableStream<Uint8Array> | number | undefined, prefix = ""): Promise<void> {
    if (!stream || typeof stream === "number") return;
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let first = true;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        state.output += `${first ? prefix : ""}${decoder.decode(value, { stream: true })}`;
        first = false;
        if (state.output.length > 256 * 1024) {
          const removed = state.output.length - 256 * 1024;
          state.output = state.output.slice(removed);
          state.cursor = Math.max(0, state.cursor - removed);
        }
      }
      state.output += decoder.decode();
    } finally { reader.releaseLock(); }
  }
}

export function shellInvocation(command: string): string[] {
  return process.platform === "win32"
    ? ["powershell.exe", "-NoProfile", "-Command", command]
    : ["bash", "-lc", command];
}
