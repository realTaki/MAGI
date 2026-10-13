import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";

const execute = promisify(execFile);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const windowsTool = (name) => path.join(process.env.SystemRoot ?? process.env.SYSTEMROOT ?? "C:\\Windows", "System32", name);

async function processTable() {
  if (process.platform !== "win32") {
    const { stdout } = await execute("/bin/ps", ["-eo", "pid=,ppid=,pgid=,sid="], { timeout: 2000, maxBuffer: 4 * 1024 * 1024 });
    return stdout.trim().split(/\r?\n/).filter(Boolean).map((line) => {
      const [pid, parent, group, session] = line.trim().split(/\s+/).map(Number);
      return { pid, parent, group, session, birth: null };
    });
  }
  const shell = windowsTool("WindowsPowerShell/v1.0/powershell.exe");
  const script = "Get-CimInstance Win32_Process | ForEach-Object { [pscustomobject]@{pid=[int]$_.ProcessId;parent=[int]$_.ParentProcessId;birth=([DateTimeOffset]$_.CreationDate).ToUnixTimeMilliseconds()} } | ConvertTo-Json -Compress";
  const { stdout } = await execute(shell, ["-NoProfile", "-NonInteractive", "-Command", script], { timeout: 5000, maxBuffer: 4 * 1024 * 1024 });
  const value = JSON.parse(stdout || "[]");
  return Array.isArray(value) ? value : [value];
}
let tableInFlight = null;
function sharedProcessTable() {
  tableInFlight ??= processTable().finally(() => { tableInFlight = null; });
  return tableInFlight;
}

/** Private ownership of roots and their descendants; no process-name matching. */
export function createProcessOwner({ spawnProcess = spawn, graceMs = 3000, forceMs = 5000 } = {}) {
  const roots = new Map();
  const reaps = new WeakMap();
  const pendingReaps = new Set();
  const pendingSignals = new Set();
  let closing = false, closePromise = null;
  const assertOpen = () => { if (closing) throw new Error("Shell is closing; new process work is refused"); };

  function ownSpawn(file, args, options = {}) {
    assertOpen();
    const before = Date.now();
    const child = spawnProcess(file, args, { ...options, detached: process.platform !== "win32" });
    const record = { child, pid: child.pid, before, after: Date.now(), exited: null, known: new Map() };
    roots.set(child, record);
    child.once("exit", () => {
      record.exited = Date.now();
      void reap(child).catch(() => {}); // command/close surfaces cleanup failures
    });
    child.once("error", () => { if (!record.pid) roots.delete(child); });
    return child;
  }

  function select(record, table) {
    const own = new Map();
    const root = table.find((item) => item.pid === record.pid);
    if (process.platform !== "win32") {
      for (const item of table) if (item.group === record.pid || item.session === record.pid) own.set(item.pid, item);
    } else if (root && root.birth >= record.before - 2 && root.birth <= record.after + 2) {
      own.set(root.pid, root);
    }
    for (const item of table) {
      const known = record.known.get(item.pid);
      if (known && known.birth === item.birth) own.set(item.pid, item);
    }
    // Windows retains the original parent PID for an orphan. The creation-time
    // fence excludes children of a later process that reused a retired root PID.
    const ceiling = record.exited ?? Infinity;
    for (const item of table) {
      if (item.parent === record.pid && (item.birth === null || (item.birth >= record.before - 2 && item.birth <= ceiling))) own.set(item.pid, item);
    }
    let added = true;
    while (added) {
      added = false;
      for (const item of table) {
        const parent = own.get(item.parent);
        if (!own.has(item.pid) && parent && (item.birth === null || item.birth >= parent.birth)) { own.set(item.pid, item); added = true; }
      }
    }
    for (const item of own.values()) record.known.set(item.pid, item);
    return [...own.values()];
  }

  async function terminate(record, force) {
    if (!record?.pid) return;
    if (process.platform !== "win32") {
      try { process.kill(-record.pid, force ? "SIGKILL" : "SIGTERM"); } catch (error) { if (error.code !== "ESRCH") throw error; }
      // Separate sessions created by owned descendants are still owned if they
      // were observed before reparenting. Normal npm descendants share the group.
      const table = await sharedProcessTable();
      for (const item of select(record, table)) {
        try { process.kill(item.pid, force ? "SIGKILL" : "SIGTERM"); } catch (error) { if (error.code !== "ESRCH") throw error; }
      }
      return;
    }
    let table = await sharedProcessTable();
    if (record.child.exitCode === null && record.child.signalCode === null && !table.some((item) => item.pid === record.pid)) table = await processTable();
    const owned = select(record, table);
    // A live root is verified against its creation time before scoped /T.
    const ids = new Set(owned.map((item) => item.pid));
    const targets = owned.some((item) => item.pid === record.pid) ? [record.pid] : owned.filter((item) => !ids.has(item.parent)).map((item) => item.pid);
    await Promise.all(targets.map(async (pid) => {
      try { await execute(windowsTool("taskkill.exe"), ["/PID", String(pid), "/T", "/F"], { timeout: 5000, windowsHide: true }); }
      catch (error) { if (![128, 255].includes(error.code)) throw error; }
    }));
  }

  function reap(child) {
    if (reaps.has(child)) return reaps.get(child);
    const record = roots.get(child);
    if (!record) return Promise.resolve();
    const operation = terminate(record, true).then(() => { roots.delete(child); });
    reaps.set(child, operation); pendingReaps.add(operation);
    void operation.finally(() => pendingReaps.delete(operation)).catch(() => {});
    return operation;
  }

  function signal(child) {
    const operation = terminate(roots.get(child), false);
    pendingSignals.add(operation);
    void operation.finally(() => pendingSignals.delete(operation)).catch(() => {});
    return operation;
  }

  function command(file, args, { cwd, env, description = "Command failed" } = {}) {
    assertOpen();
    const child = ownSpawn(file, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    roots.get(child).command = true;
    let output = "";
    const collect = (chunk) => { output = `${output}${String(chunk)}`.slice(-64_000); };
    child.stdout?.on("data", collect); child.stderr?.on("data", collect);
    return new Promise((resolve, reject) => {
      child.once("error", (error) => reject(new Error(`${description}: ${error.message}`)));
      child.once("close", (code, signal) => {
        void reap(child).then(() => {
          if (closing) reject(new Error(`${description}: interrupted because Shell is closing`));
          else if (code === 0) resolve(output);
          else reject(new Error(`${description} (${code ?? signal ?? "unknown"})${output.trim() ? `: ${output.trim()}` : ""}`));
        }, reject);
      });
    });
  }

  async function closeOnce() {
    closing = true;
    const records = [...roots.values()];
    // Applications may already have received graceful shutdown from Supervisor.
    // Commands get a signal immediately; force cleanup follows the finite grace.
    await Promise.allSettled(records.filter((record) => record.command).map((record) => terminate(record, false)));
    const deadline = Date.now() + graceMs;
    while (Date.now() < deadline && records.some((record) => record.child.exitCode === null && record.child.signalCode === null)) await sleep(20);
    const failures = await Promise.allSettled([...records.map((record) => terminate(record, true)), ...pendingReaps]);
    const forceDeadline = Date.now() + forceMs;
    while (Date.now() < forceDeadline && records.some((record) => record.child.exitCode === null && record.child.signalCode === null)) await sleep(20);
    const tails = await Promise.allSettled([...pendingSignals, ...pendingReaps]);
    const errors = failures.filter((result) => result.status === "rejected").map((result) => result.reason);
    errors.push(...tails.filter((result) => result.status === "rejected").map((result) => result.reason));
    if (records.some((record) => record.child.exitCode === null && record.child.signalCode === null)) errors.push(new Error("Owned process did not exit within shutdown deadline"));
    roots.clear();
    if (errors.length) throw new AggregateError(errors, "Shell process cleanup failed");
  }
  return { spawn: ownSpawn, command, signal, reap, assertOpen, beginClose: () => { closing = true; },
    get closing() { return closing; },
    close: () => closePromise ??= closeOnce(),
  };
}
