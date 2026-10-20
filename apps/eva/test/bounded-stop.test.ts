import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { Bus, SYSTEM_CONTACT_ID } from "@magi/bus";
import { MUTATION_SETTLE_MS } from "@magi/bus/abort.js";
import { AgentWorker } from "@magi/agent/worker.js";
import { ToolsWorker } from "@magi/built-in-tools/worker.js";
import { ProvidersWorker } from "@magi/providers/worker.js";
import { ManagerWorker } from "@magi/manager";

async function eventually(check: () => boolean) {
  for (let i = 0; i < 500; i++) { if (check()) return; await sleep(10); }
  assert.fail("condition did not become true");
}

for (const mode of ["stop", "suspendForReplace", "tool timeout"] as const) test(`${mode} bounds orphaned mutations across calls and chats without replay`, { timeout: 15_000 }, async (t) => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-orphan-stop-"));
  let bus = new Bus("@orphan-stop.magi", workspace);
  const calls = ["write_file", "edit_file", "write_file"].map((name, i) => ({ name, tool_call_id: `call-${i}`, arguments: {} }));
  bus.tools.registerSource("orphan", () => calls.slice(0, 2).map((call) => ({ name: call.name, description: "orphan", input_schema: {}, async run() { throw new Error("must not execute"); } })));
  const claimed: number[] = [];
  let polling = true;
  const orphan = { worker_name: "orphan", async poll() {
    if (!polling) return false;
    const job = bus.board("RunToolJob").claim("orphan");
    if (!job) return false;
    claimed.push(job.id); return true;
  }, interrupt() { polling = false; }, stop() { polling = false; } };
  let modelCalls = 0;
  const manager = new ManagerWorker(bus, [new AgentWorker(bus), new ProvidersWorker(bus, { async complete() { modelCalls++; return { role: "assistant", content: "", tool_calls: calls }; } }), orphan], { drain: ["agent"], build: { app: "eva", script: "build" } });
  const turns: number[] = [];
  try {
    await manager.start();
    for (let i = 0; i < 2; i++) {
      const chat = bus.chats.forChannel("cli", `chat-${i}`);
      turns.push(bus.board("MessageDeliveryJob").publish({ message_id: bus.messages.add(chat.id, SYSTEM_CONTACT_ID, "mutate") }, "test"));
    }
    await eventually(() => claimed.length === 6);
    const start = performance.now();
    if (mode === "tool timeout") {
      const now = Date.now();
      t.mock.method(Date, "now", () => now + 120_001);
      await eventually(() => turns.every((id) => bus.board("MessageDeliveryJob").result(id)?.status === "failed"));
      t.mock.restoreAll();
      await manager.stop();
    } else await manager[mode]();
    assert.ok(performance.now() - start < MUTATION_SETTLE_MS * 2.5, "one aggregate grace period, not one per mutation or chat");
    assert.equal(modelCalls, 2);
    for (const turn of turns) {
      assert.equal(bus.board("MessageDeliveryJob").result(turn)?.status, "failed");
      const results = bus.agentTurns.get(turn).messages.filter((m) => m.role === "tool");
      assert.equal(results.length, 3);
      assert.ok(results.every((m) => m.is_error && /outcome is unknown/.test(m.content)));
    }
    const board = bus.board("RunToolJob");
    assert.equal(board.result(claimed[0]!), null, "do not steal a claimed mutation's outcome");
    assert.equal(board.submit("orphan", claimed[0]!, { output: { content: "late commit" } }), true);
    assert.equal(board.submit("orphan", claimed[0]!, { output: { content: "duplicate" } }), false);
    assert.equal(board.claim("another-worker"), null, "never requeue orphaned claims");
    await manager.stop();
    bus.close();
    for (let i = 0; i < 2; i++) {
      bus = new Bus("@orphan-stop.magi", workspace);
      assert.equal(bus.board("RunToolJob").claim("restart"), null);
      for (const turn of turns) assert.equal(bus.board("MessageDeliveryJob").claimId("agent", turn), null);
      for (const turn of turns) assert.equal(bus.agentTurns.get(turn).messages.filter((m) => m.role === "tool").length, 3);
      if (i === 0) bus.close();
    }
  } finally { t.mock.restoreAll(); await manager.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

for (const mode of ["stop", "suspendForReplace"] as const) for (const outcome of ["completed", "failed"] as const) test(`${mode} refuses unsafe live mutation shutdown, retains ownership, then retries after ${outcome}`, { timeout: 15_000 }, async (t) => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-live-stop-"));
  const bus = new Bus("@live-stop.magi", workspace);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let toolId = 0;
  const board = bus.board("RunToolJob");
  const publish = board.publish.bind(board);
  t.mock.method(board, "publish", (...args: Parameters<typeof board.publish>) => { toolId = publish(...args); return toolId; });
  let runs = 0;
  let remainingStopped = false;
  const tools = new ToolsWorker(bus, [{ name: "edit_file", description: "uncancellable", input_schema: {}, async run() {
    runs++; await gate;
    // Demonstrates that BUS is retained for the live owner until it has settled.
    bus.settings.set("late.mutation", outcome);
    if (outcome === "failed") throw new Error("late mutation failure");
    return "late committed";
  } }]);
  const manager = new ManagerWorker(bus, [new AgentWorker(bus), new ProvidersWorker(bus, { async complete() { return { role: "assistant", content: "", tool_calls: [{ name: "edit_file", tool_call_id: "edit", arguments: {} }] }; } }), tools,
    { worker_name: "last-worker", async poll() { return false; }, stop() { remainingStopped = true; } }], { drain: ["agent"], build: { app: "eva", script: "build" } });
  try {
    await manager.start();
    const chat = bus.chats.forChannel("cli", "test");
    const turn = bus.board("MessageDeliveryJob").publish({ message_id: bus.messages.add(chat.id, SYSTEM_CONTACT_ID, "edit") }, "test");
    await eventually(() => runs === 1);
    await assert.rejects(manager[mode](), /Cannot stop tools safely.*outcome unknown/);
    assert.equal(remainingStopped, true, "one failed stop must not skip the remaining workers");
    assert.equal(manager.isRunning("tools"), false);
    assert.notEqual(manager.healthy(), null);
    assert.throws(() => tools.start(), /still owns work/);
    if (mode === "suspendForReplace") await assert.rejects(manager.resume(), /Cannot stop tools safely/);
    else await assert.rejects(manager.start(), /Cannot stop tools safely/);
    const result = bus.agentTurns.get(turn).messages.filter((m) => m.role === "tool");
    assert.equal(result.length, 1);
    assert.match(result[0]!.content, /outcome is unknown/);
    assert.equal(bus.board("RunToolJob").claim("replay"), null);
    release();
    await eventually(() => bus.settings.get("late.mutation") === outcome);
    await manager.stop();
    assert.equal(runs, 1);
    assert.ok(toolId);
    assert.equal(board.result(toolId)?.status, outcome);
    assert.equal(board.submit("tools", toolId, { output: { content: "duplicate" } }), false);
    assert.equal(bus.agentTurns.get(turn).messages.filter((m) => m.role === "tool").length, 1);
    assert.match(bus.agentTurns.get(turn).messages.find((m) => m.role === "tool")!.content, /outcome is unknown/, "late result must not rewrite the interrupted turn");
  } finally { release(); await manager.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

test("interruption cancels unclaimed mutations without invoking them", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-pending-stop-"));
  const bus = new Bus("@pending-stop.magi", workspace);
  bus.tools.registerSource("absent", () => [{ name: "write_file", description: "absent", input_schema: {}, async run() { throw new Error("must not execute"); } }]);
  const manager = new ManagerWorker(bus, [new AgentWorker(bus), new ProvidersWorker(bus, { async complete() { return { role: "assistant", content: "", tool_calls: [{ name: "write_file", tool_call_id: "write", arguments: {} }] }; } })], { drain: ["agent"], build: { app: "eva", script: "build" } });
  try {
    await manager.start();
    const chat = bus.chats.forChannel("cli", "test");
    const turn = bus.board("MessageDeliveryJob").publish({ message_id: bus.messages.add(chat.id, SYSTEM_CONTACT_ID, "write") }, "test");
    await eventually(() => bus.agentTurns.has(turn) && Boolean(bus.agentTurns.get(turn).messages.find((m) => m.tool_calls?.length)));
    await manager.stop();
    assert.equal(bus.board("RunToolJob").claim("late-worker"), null);
    const results = bus.agentTurns.get(turn).messages.filter((m) => m.role === "tool");
    assert.equal(results.length, 1);
    assert.equal(results[0]!.is_error, true);
    assert.doesNotMatch(results[0]!.content, /outcome is unknown/);
  } finally { await manager.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});


test("Shell retries a rejected stop without closing BUS prematurely", async () => {
  const { Shell } = await import("@magi/node/shell.js");
  let attempts = 0;
  let closed = 0;
  // Isolate the lifecycle owner from unrelated startup/network dependencies.
  const shell = Object.assign(Object.create(Shell.prototype), {
    ready: null, stopping: null, stopRequested: false,
    manager: { async stop() { if (++attempts === 1) throw new Error("mutation still active"); } },
    bus: { close() { closed++; } },
  }) as InstanceType<typeof Shell>;
  const results = await Promise.allSettled([shell.stop(), shell.stop()]);
  assert.ok(results.every((result) => result.status === "rejected"));
  assert.equal(attempts, 1);
  assert.equal(closed, 0);
  assert.equal(shell.stopped, true, "new API work remains blocked while shutdown can be retried");
  await shell.stop();
  assert.equal(attempts, 2);
  assert.equal(closed, 1);
  await shell.stop();
  assert.equal(closed, 1);
});

test("replace attempts safe recovery after a partially failed suspension", { timeout: 15_000 }, async () => {
  const { ReplaceWorker } = await import("@magi/manager/replace.js");
  const workspace = await mkdtemp(join(tmpdir(), "magi-partial-suspend-"));
  const bus = new Bus("@partial-suspend.magi", join(workspace, "state"));
  const app = join(workspace, "apps", "eva");
  await mkdir(join(app, "dist"), { recursive: true });
  await writeFile(join(app, "dist", "eva.js"), "// valid entry, must not activate\n");
  await writeFile(join(app, "package.json"), JSON.stringify({ scripts: { build: `"${process.execPath}" --version` } }));
  let suspends = 0;
  let resumes = 0;
  const worker = new ReplaceWorker(bus, { app: "eva", script: "build" }, {
    async suspendForReplace() { suspends++; throw new Error("mutation still active"); },
    async resume() { resumes++; throw new Error("unsafe to resume until mutation settles"); },
    healthy() { throw new Error("must not activate a partial suspension"); },
  });
  try {
    const board = bus.board("ReplaceSelfJob");
    const id = board.publish({ checkout: workspace }, "test");
    await worker.poll();
    await eventually(() => board.result(id) !== null);
    assert.equal(suspends, 1);
    assert.equal(resumes, 1);
    assert.equal(board.result(id)?.status, "failed");
    assert.match(board.result(id)?.error ?? "", /mutation still active; unsafe to resume/);
  } finally { bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

for (const scenario of ["recover", "recover failed mutation", "deliberate stop", "new suspension", "stop during recovered start", "stop during hookless recovered start"] as const) test(`failed replacement resumes automatically after settlement: ${scenario}`, { timeout: 25_000 }, async (t) => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-deferred-resume-"));
  const bus = new Bus("@deferred-resume.magi", join(workspace, "state"));
  const app = join(workspace, "apps", "eva");
  await mkdir(join(app, "dist"), { recursive: true });
  await writeFile(join(app, "dist", "eva.js"), "// valid build; failed suspension must never activate it\n");
  await writeFile(join(app, "package.json"), JSON.stringify({ scripts: { build: `"${process.execPath}" --version` } }));
  let release!: () => void;
  let releaseStart!: () => void;
  const mutation = new Promise<void>((resolve) => { release = resolve; });
  const heldStart = new Promise<void>((resolve) => { releaseStart = resolve; });
  let mutationRuns = 0;
  let settled = false;
  let toolStarts = 0;
  let helperStarts = 0;
  let helperStops = 0;
  let helperPolls = 0;
  const tools = new ToolsWorker(bus, [{ name: "edit_file", description: "uncancellable", input_schema: {}, async run() {
    mutationRuns++;
    await mutation;
    bus.settings.set("late.owner", "finished");
    settled = true;
    if (scenario === "recover failed mutation") throw new Error("mutation failed after shutdown grace");
    return "mutation committed";
  } }]);
  const startTools = tools.start.bind(tools);
  t.mock.method(tools, "start", () => {
    if (++toolStarts > 1) assert.equal(settled, true, "old owner must settle before the next tool generation");
    startTools();
  });
  const helper = { worker_name: "helper", async start() {
    helperStarts++;
    if (helperStarts > 1 && scenario.startsWith("stop during")) await heldStart;
  }, async poll() { helperPolls++; return false; }, stop: scenario === "stop during hookless recovered start" ? undefined : () => {
    // A cancelled cold generation must be retired before its BUS can close.
    bus.settings.set("helper.stops", String(++helperStops));
  } };
  const manager = new ManagerWorker(bus, [new AgentWorker(bus), new ProvidersWorker(bus, { async complete(request) { if (request.messages.at(-1)?.content.includes("source error:")) return { role: "assistant", content: "NO_REPLY" }; return { role: "assistant", content: "", tool_calls: [{ name: "edit_file", tool_call_id: "mutation", arguments: {} }] }; } }), tools, helper], { drain: ["agent"], build: { app: "eva", script: "build" } });
  try {
    await manager.start();
    const chat = bus.chats.forChannel("cli", "test");
    bus.board("MessageDeliveryJob").publish({ message_id: bus.messages.add(chat.id, SYSTEM_CONTACT_ID, "edit") }, "test");
    await eventually(() => mutationRuns === 1);
    const board = bus.board("ReplaceSelfJob");
    const id = board.publish({ checkout: workspace }, "test");
    await eventually(() => board.result(id)?.status === "failed");
    assert.match(board.result(id)?.error ?? "", /Cannot stop tools safely/);
    assert.equal(manager.isRunning("agent"), false);
    assert.equal(manager.isRunning("tools"), false);
    assert.equal(manager.isRunning("helper"), false);
    await sleep(50);
    assert.equal(helperStarts, 1, "supervisor must not retry while the mutation is still active");
    assert.equal(toolStarts, 1);

    if (scenario === "new suspension") {
      await assert.rejects(manager.suspendForReplace(), /Cannot stop tools safely/);
      release();
      await sleep(100);
      assert.equal(helperStarts, 1, "a newer suspension cancels the failed replacement's recovery intent");
      assert.equal(toolStarts, 1);
      assert.equal(manager.isRunning("agent"), false);
    } else if (scenario === "deliberate stop") {
      const stopping = manager.stop();
      release();
      await stopping;
      await sleep(80);
      assert.equal(helperStarts, 1);
      assert.equal(toolStarts, 1);
      assert.equal(manager.isRunning("agent"), false);
    } else {
      release();
      if (scenario.startsWith("stop during")) {
        await eventually(() => helperStarts === 2);
        let stopped = false;
        const stopping = manager.stop().then(() => { stopped = true; });
        await sleep(30);
        assert.equal(stopped, false, "stop must wait for the recovering cold generation");
        releaseStart();
        await stopping;
        assert.equal(helperStops, scenario === "stop during hookless recovered start" ? 0 : 2);
        assert.doesNotMatch(manager.healthy() ?? "", /still stopping/, "hookless cold generations do not leave retained ownership");
        assert.equal(manager.isRunning("helper"), false);
        assert.equal(manager.isRunning("tools"), false);
        const polls = helperPolls;
        await sleep(80);
        assert.equal(helperPolls, polls, "the cancelled generation never acquires a poll loop");
      } else {
        await eventually(() => manager.isRunning("agent") && manager.isRunning("tools") && manager.isRunning("helper"));
        await sleep(100);
        assert.equal(toolStarts, 2);
        assert.equal(helperStarts, 2, "recovery occurs once, without a manual resume call");
        assert.equal(manager.healthy(), null);
      }
    }
    assert.equal(mutationRuns, 1, "recovery never replays the old mutation");
  } finally { release(); releaseStart(); await manager.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});

test("a replacement resume arriving after deliberate stop cannot arm new recovery", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-late-resume-"));
  const bus = new Bus("@late-resume.magi", workspace);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let starts = 0;
  let polled = false;
  const worker = { worker_name: "held", start() { starts++; }, async poll() { polled = true; await held; return false; } };
  const manager = new ManagerWorker(bus, [worker], { drain: ["held"], build: { app: "eva", script: "build" } });
  try {
    await manager.start();
    await eventually(() => polled);
    const stopping = manager.stop();
    await eventually(() => !manager.isRunning("held"));
    assert.equal(manager.up, true, "deliberate stop is still draining its old generation");
    await manager.resume(); // Simulates the failed replacement's late catch handler.
    assert.equal(starts, 1);
    release(); await stopping;
    await sleep(50);
    assert.equal(starts, 1);
  } finally { release(); await manager.stop(); bus.close(); await rm(workspace, { recursive: true, force: true }); }
});
