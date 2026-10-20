import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { Bus, SYSTEM_CONTACT_ID } from "@magi/bus";
import { ShellManager } from "@magi/built-in-tools/shellManager.js";
import { McpWorker } from "@magi/mcp/worker.js";

async function eventually(check: () => Promise<boolean> | boolean) {
  for (let i = 0; i < 200; i++) { if (await check()) return; await sleep(10); }
  assert.fail("condition did not become true");
}

test("shutdown kills a TERM-resistant bash descendant even after its leader exits", { skip: process.platform === "win32" }, async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-shell-stop-"));
  const shells = new ShellManager();
  let pid = 0;
  try {
    shells.start(`bash -c 'trap "" TERM; echo $$ > child.pid; while :; do sleep 1; done' & wait`, workspace);
    await eventually(async () => { try { pid = Number(await readFile(join(workspace, "child.pid"), "utf8")); return pid > 0; } catch { return false; } });
    await shells.shutdown();
    await eventually(async () => {
      try { process.kill(pid, 0); } catch { return true; }
      // Linux container PID 1 may defer reaping adopted zombies; a zombie executes no work.
      try { return /\) Z /.test(await readFile(`/proc/${pid}/stat`, "utf8")); } catch { return false; }
    });
    assert.deepEqual(shells.list(), []);
  } finally { await shells.shutdown(); await rm(workspace, { recursive: true, force: true }); }
});

test("MCP stop propagates AbortSignal and completes pending tool jobs", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-mcp-stop-"));
  const bus = new Bus("@interrupt.magi", workspace);
  let signal: AbortSignal | undefined;
  let closed = false;
  bus.mcpServers.save({ name: "mock", connection_type: "stdio", command: "mock", args: [], url: null, env: {}, headers: {}, enabled: true, connect_timeout: null, execute_timeout: null });
  const worker = new McpWorker(bus, async () => ({
    tools: [{ name: "mock__wait", description: "wait", input_schema: {}, async run(_args, received) {
      signal = received;
      return new Promise<string>((_resolve, reject) => received?.addEventListener("abort", () => reject(received.reason), { once: true }));
    } }], async close() { closed = true; },
  }));
  try {
    await worker.start();
    const board = bus.board("RunToolJob");
    const id = board.publish({ call: { name: "mock__wait", tool_call_id: "wait", arguments: {} } }, "test");
    await worker.poll();
    await worker.stop();
    assert.equal(signal?.aborted, true);
    assert.equal(closed, true);
    assert.equal(board.result(id)?.status, "failed");
    await worker.start();
    assert.equal(await worker.poll(), false);
  } finally { await worker.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

test("restart terminalizes claimed turns and model calls without retrying tool side effects", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-turn-restart-"));
  let bus = new Bus("@restart.magi", workspace);
  try {
    const chat = bus.chats.forChannel("cli", "terminal");
    const message = bus.messages.add(chat.id, SYSTEM_CONTACT_ID, "do something");
    const turn = bus.board("MessageDeliveryJob").publish({ message_id: message }, "test");
    bus.board("MessageDeliveryJob").claimId("agent", turn);
    bus.agentTurns.ensure(turn, { messages: [], tools: [] });
    const llm = bus.board("CallLLMJob").publish({ turn_id: turn }, "agent");
    bus.board("CallLLMJob").claimId("providers", llm);
    bus.agentTurns.appendAssistant(turn, llm, { role: "assistant", content: "", tool_calls: [{ tool_call_id: "effect", name: "bash", arguments: { command: "touch result" } }] });
    const tool = bus.board("RunToolJob").publish({ call: { tool_call_id: "effect", name: "bash", arguments: {} } }, "agent");
    const unstarted = bus.board("MessageDeliveryJob").publish({ message_id: bus.messages.add(chat.id, SYSTEM_CONTACT_ID, "queued") }, "test");
    bus.board("MessageDeliveryJob").claimId("agent", unstarted);
    bus.close();
    bus = new Bus("@restart.magi", workspace);
    assert.equal(bus.board("MessageDeliveryJob").result(turn)?.status, "failed");
    assert.ok(bus.board("MessageDeliveryJob").claimId("agent", unstarted));
    bus.board("MessageDeliveryJob").release("agent", unstarted);
    assert.equal(bus.board("CallLLMJob").result(llm)?.status, "failed");
    assert.equal(bus.board("CallLLMJob").claim("providers"), null);
    assert.equal(bus.board("RunToolJob").result(tool)?.status, "failed");
    const results = bus.agentTurns.get(turn).messages.filter((m) => m.role === "tool");
    assert.equal(results.length, 1);
    assert.equal(results[0]?.tool_call_id, "effect");
    assert.equal(results[0]?.is_error, true);
    assert.ok(bus.messages.list(chat.id, 20).some((m) => m.content.includes("was not retried")));
    bus.close();
    bus = new Bus("@restart.magi", workspace);
    assert.equal(bus.agentTurns.get(turn).messages.filter((m) => m.role === "tool").length, 1);
  } finally { bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

test("launcher ownership gate reaps shell groups when the EVA worker is SIGKILLed", { skip: process.platform === "win32" }, async () => {
  const { spawn } = await import("node:child_process");
  const { runInNewContext } = await import("node:vm");
  const { fileURLToPath } = await import("node:url");
  const workspace = await mkdtemp(join(tmpdir(), "magi-supervised-stop-"));
  // Execute the actual launcher's ownership handlers against a real IPC child.
  const source = await readFile(new URL("../../eva.mjs", import.meta.url), "utf8");
  const noteSource = source.slice(source.indexOf("function note(child)"), source.indexOf("function waitFor(child"));
  const reapSource = source.slice(source.indexOf("function reapToolProcesses(child)"), source.indexOf("function killTree(child)"));
  const handlers = runInNewContext(`${noteSource}\n${reapSource}\n({ note, reapToolProcesses })`, { LAUNCHER_PROTOCOL: 1, process, spawn }) as { note(child: unknown): void; reapToolProcesses(child: unknown): void };
  const shellModule = fileURLToPath(new URL("../../../../packages/built-in-tools/dist/shellManager.js", import.meta.url));
  const child = spawn(process.execPath, ["--input-type=module", "-e", `import { ShellManager } from ${JSON.stringify(shellModule)}; new ShellManager().start('echo $$ > owned.pid; while :; do sleep 1; done', ${JSON.stringify(workspace)});`], { env: { ...process.env, MAGI_TOOL_PROCESS_SUPERVISOR_PID: String(process.pid) }, stdio: ["ignore", "ignore", "ignore", "ipc"] });
  handlers.note(child);
  let pid = 0;
  try {
    await eventually(async () => { try { pid = Number(await readFile(join(workspace, "owned.pid"), "utf8")); return pid > 0; } catch { return false; } });
    const exited = new Promise<void>((resolve) => child.once("exit", () => { handlers.reapToolProcesses(child); resolve(); }));
    child.kill("SIGKILL");
    await exited;
    await eventually(async () => {
      try { process.kill(pid, 0); } catch { return true; }
      try { return /\) Z /.test(await readFile(`/proc/${pid}/stat`, "utf8")); } catch { return false; }
    });
  } finally {
    child.kill("SIGKILL"); handlers.reapToolProcesses(child);
    await rm(workspace, { recursive: true, force: true });
  }
});

for (const mode of ["stop", "suspendForReplace"] as const) test(`manager ${mode} interrupts active shell turns and records their tool result`, { skip: process.platform === "win32" }, async () => {
  const { ManagerWorker } = await import("@magi/manager");
  const { AgentWorker } = await import("@magi/agent/worker.js");
  const { ToolsWorker } = await import("@magi/built-in-tools/worker.js");
  const { ProvidersWorker } = await import("@magi/providers/worker.js");
  const workspace = await mkdtemp(join(tmpdir(), "magi-manager-interrupt-"));
  const bus = new Bus("@manager-interrupt.magi", workspace);
  let modelCalls = 0;
  const manager = new ManagerWorker(bus, [new AgentWorker(bus), new ToolsWorker(bus), new ProvidersWorker(bus, { async complete() {
    modelCalls++;
    return { role: "assistant", content: "", tool_calls: [{ name: "bash", tool_call_id: "blocking", arguments: { command: "echo $$ > active.pid; sleep 120" } }] };
  } })], { drain: ["agent"], build: { app: "eva", script: "build" } });
  try {
    await manager.start();
    const chat = bus.chats.forChannel("cli", "terminal");
    const message = bus.messages.add(chat.id, SYSTEM_CONTACT_ID, "run");
    const turn = bus.board("MessageDeliveryJob").publish({ message_id: message }, "test");
    await eventually(async () => { try { return Number(await readFile(join(workspace, "active.pid"), "utf8")) > 0; } catch { return false; } });
    const started = Date.now();
    await manager[mode]();
    assert.ok(Date.now() - started < 3000);
    assert.equal(modelCalls, 1);
    assert.equal(bus.board("MessageDeliveryJob").result(turn)?.status, "failed");
    const results = bus.agentTurns.get(turn).messages.filter((m) => m.role === "tool");
    assert.equal(results.length, 1);
    assert.equal(results[0]?.is_error, true);
    assert.equal(bus.board("RunToolJob").claim("tools"), null);
  } finally { await manager.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

for (const name of ["write_file", "edit_file"]) test(`ToolsWorker stop waits for an uncancellable ${name} mutation`, async () => {
  const { ToolsWorker } = await import("@magi/built-in-tools/worker.js");
  const { writeFile } = await import("node:fs/promises");
  const workspace = await mkdtemp(join(tmpdir(), "magi-write-stop-"));
  const bus = new Bus("@write-stop.magi", workspace);
  let release!: () => void;
  let interrupted!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const cancelled = new Promise<void>((resolve) => { interrupted = resolve; });
  const worker = new ToolsWorker(bus, [{ name, description: "uncancellable file mutation", input_schema: {}, async run(_args, signal) {
    signal?.addEventListener("abort", interrupted, { once: true });
    await gate;
    await writeFile(join(workspace, "result.txt"), "finished");
    return "written";
  } }]);
  try {
    worker.start();
    const board = bus.board("RunToolJob");
    const id = board.publish({ call: { name, tool_call_id: "mutation", arguments: {} } }, "test");
    await worker.poll();
    let stopped = false;
    const stopping = worker.stop().then(() => { stopped = true; });
    await cancelled;
    await sleep(20);
    assert.equal(stopped, false, "shutdown must retain the raw mutation promise");
    release();
    await stopping;
    assert.equal(await readFile(join(workspace, "result.txt"), "utf8"), "finished");
    assert.equal(board.result(id)?.status, "completed");
  } finally { release(); await worker.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

for (const mode of ["stop", "suspendForReplace"] as const) test(`${mode} retains a second same-chat turn for restart`, async () => {
  const { ManagerWorker } = await import("@magi/manager");
  const { AgentWorker } = await import("@magi/agent/worker.js");
  const { ProvidersWorker } = await import("@magi/providers/worker.js");
  const workspace = await mkdtemp(join(tmpdir(), "magi-queued-restart-"));
  let bus = new Bus("@queued.magi", workspace);
  let manager = new ManagerWorker(bus, [new AgentWorker(bus)], { drain: ["agent"], build: { app: "eva", script: "build" } });
  try {
    const chat = bus.chats.forChannel("cli", "terminal");
    const turns = ["first", "second"].map((text) => bus.board("MessageDeliveryJob").publish({ message_id: bus.messages.add(chat.id, SYSTEM_CONTACT_ID, text) }, "test"));
    await manager.start();
    await eventually(() => bus.agentTurns.has(turns[0]!));
    await sleep(50);
    await manager[mode]();
    assert.equal(bus.board("MessageDeliveryJob").result(turns[0]!)?.status, "failed");
    assert.equal(bus.board("MessageDeliveryJob").result(turns[1]!), null);
    assert.equal(bus.agentTurns.has(turns[1]!), false);
    assert.equal(bus.messages.list(chat.id, 20).filter((m) => m.content === "MAGI stopped").length, 1);
    await manager.stop(); bus.close();
    bus = new Bus("@queued.magi", workspace);
    let modelCalls = 0;
    manager = new ManagerWorker(bus, [new AgentWorker(bus), new ProvidersWorker(bus, { async complete() { modelCalls++; return { role: "assistant", content: "second completed" }; } })], { drain: ["agent"], build: { app: "eva", script: "build" } });
    await manager.start();
    await eventually(() => bus.board("MessageDeliveryJob").result(turns[1]!)?.status === "completed");
    assert.equal(modelCalls, 1);
  } finally { await manager.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

test("interrupted agent records the actual result of an active uncancellable edit", async () => {
  const { ManagerWorker } = await import("@magi/manager");
  const { AgentWorker } = await import("@magi/agent/worker.js");
  const { ProvidersWorker } = await import("@magi/providers/worker.js");
  const { ToolsWorker } = await import("@magi/built-in-tools/worker.js");
  const { writeFile } = await import("node:fs/promises");
  const workspace = await mkdtemp(join(tmpdir(), "magi-edit-result-"));
  const bus = new Bus("@edit-result.magi", workspace);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let editing = false;
  const manager = new ManagerWorker(bus, [new AgentWorker(bus), new ProvidersWorker(bus, { async complete() { return { role: "assistant", content: "", tool_calls: [{ name: "edit_file", tool_call_id: "edit", arguments: {} }] }; } }), new ToolsWorker(bus, [{ name: "edit_file", description: "edit", input_schema: {}, async run() { editing = true; await gate; await writeFile(join(workspace, "changed.txt"), "committed"); return "Edit committed"; } }])], { drain: ["agent"], build: { app: "eva", script: "build" } });
  try {
    await manager.start();
    const chat = bus.chats.forChannel("cli", "terminal");
    const turn = bus.board("MessageDeliveryJob").publish({ message_id: bus.messages.add(chat.id, SYSTEM_CONTACT_ID, "edit") }, "test");
    await eventually(() => editing);
    let stopped = false;
    const stopping = manager.stop().then(() => { stopped = true; });
    await sleep(30); assert.equal(stopped, false);
    release(); await stopping;
    const result = bus.agentTurns.get(turn).messages.find((m) => m.role === "tool");
    assert.equal(await readFile(join(workspace, "changed.txt"), "utf8"), "committed");
    assert.equal(result?.content, "Edit committed");
    assert.equal(result?.is_error, false);
  } finally { release(); await manager.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

test("unrelated IPC parent gets a clear shell ownership error without spawning a command", async () => {
  const { spawn } = await import("node:child_process");
  const { fileURLToPath } = await import("node:url");
  const workspace = await mkdtemp(join(tmpdir(), "magi-unrelated-ipc-"));
  const modulePath = fileURLToPath(new URL("../../../../packages/built-in-tools/dist/shellManager.js", import.meta.url));
  const env = { ...process.env }; delete env.MAGI_TOOL_PROCESS_SUPERVISOR_PID; delete env.MAGI_LAUNCHER;
  const child = spawn(process.execPath, ["--input-type=module", "-e", `import { ShellManager } from ${JSON.stringify(modulePath)}; try { new ShellManager().start('touch should-not-exist', ${JSON.stringify(workspace)}); } catch (error) { process.send({ error: error.message }); } process.disconnect();`], { env, stdio: ["ignore", "ignore", "inherit", "ipc"] });
  try {
    const received = new Promise<{ error: string }>((resolve) => child.once("message", resolve));
    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    assert.match((await received).error, /IPC parent is not the MAGI launcher/);
    await exited;
    await assert.rejects(readFile(join(workspace, "should-not-exist")));
  } finally { child.kill("SIGKILL"); await rm(workspace, { recursive: true, force: true }); }
});

test("naturally completed background shell removes launcher ownership while keeping output", async () => {
  const { spawn } = await import("node:child_process");
  const { fileURLToPath } = await import("node:url");
  const modulePath = fileURLToPath(new URL("../../../../packages/built-in-tools/dist/shellManager.js", import.meta.url));
  const child = spawn(process.execPath, ["--input-type=module", "-e", `import { ShellManager } from ${JSON.stringify(modulePath)}; const manager = new ShellManager(); const shell = manager.start('echo naturally-done', process.cwd()); shell.process.once('close', () => setTimeout(() => { process.send({ output: manager.read(shell.id) }); process.disconnect(); }, 50));`], { env: { ...process.env, MAGI_TOOL_PROCESS_SUPERVISOR_PID: String(process.pid) }, stdio: ["ignore", "ignore", "inherit", "ipc"] });
  const events: { type?: string; action?: string; pid?: number; output?: string }[] = [];
  child.on("message", (message) => {
    const value = message as typeof events[number]; events.push(value);
    if (value.type === "tool-process" && value.action === "add") child.send({ v: 1, type: "tool-process-ready", pid: value.pid });
  });
  try {
    await new Promise<void>((resolve) => child.once("exit", () => resolve()));
    const added = events.find((e) => e.action === "add");
    assert.ok(added?.pid);
    assert.ok(events.some((e) => e.action === "remove" && e.pid === added.pid));
    assert.match(events.find((e) => e.output)?.output ?? "", /naturally-done.*\n\[status\] completed/s);
  } finally { child.kill("SIGKILL"); }
});
