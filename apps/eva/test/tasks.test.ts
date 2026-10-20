import assert from "node:assert/strict";
import { afterEach, expect, test , sleep} from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "./harness.js";
import { SYSTEM_CONTACT_ID } from "@magi/bus";
import { cronMatches } from "@magi/channel-tasks/worker.js";

const workspaces: string[] = [];
afterEach(async () => { for (const path of workspaces.splice(0)) await rm(path, { recursive: true, force: true }); });

test("the task worker carries schedule_task and a due task enters the agent", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-task-"));
  workspaces.push(workspace);
  const prompts: string[] = [];
  const delivered: string[] = [];
  const magi = new Magi("@task.magi", {
    workspace, deliver: (text) => delivered.push(text),
    client: { async complete(job) { prompts.push(job.messages.at(-1)?.content ?? ""); return { role: "assistant", content: "task complete" }; } },
  });
  try {
    // The tool travels with the task worker: offered exactly while that worker runs.
    expect(magi.bus.tools.get("schedule_task")).toBeNull();
    await magi.start();
    expect(magi.bus.tools.get("schedule_task")).not.toBeNull();

    const chat = magi.bus.chats.forChannel("test-owner", "terminal");
    const saved = await runTool(magi, "schedule_task", {
      name: "daily review", prompt: "Review the project", frequency: "daily", hour: 9, minute: 30,
      chat_id: chat.id,
    }) as { task_id: number; cron: string };
    expect(saved.cron).toBe("30 9 * * *");

    // Due now: every minute matches, so the worker's own scan fires it.
    magi.bus.tasks.update(saved.task_id, { cron: "* * * * *" });
    for (let i = 0; i < 400 && !delivered.length; i++) await sleep(10);
    expect(magi.bus.tasks.get(saved.task_id)?.last_fired_minute).toBeDefined();
    expect(prompts.join("\n")).toContain("Review the project");
    expect(prompts.join("\n")).toContain("schedule: * * * * *");
    expect(delivered).toEqual(["task complete"]);
  } finally {
    await magi.stop();
  }
});

/** What the agent does with a call: publish it and wait for whichever worker owns the tool. */
async function runTool(magi: Magi, name: string, args: Record<string, unknown>): Promise<unknown> {
  const chatId = Number(args.chat_id);
  const messageId = magi.bus.messages.add(chatId, SYSTEM_CONTACT_ID, "Schedule the requested daily project review");
  const board = magi.bus.board("RunToolJob");
  const id = board.publish({ chat_id: chatId, message_id: messageId, call: { tool_call_id: "call-1", name, arguments: args } }, "test");
  for (let i = 0; i < 200 && !board.result(id); i++) await sleep(10);
  const result = board.result(id);
  if (result?.status !== "completed") throw new Error(result?.error ?? `${name} did not complete`);
  return JSON.parse(result.output?.content ?? "null");
}

test("cron matching supports ranges, lists, and steps in UTC", () => {
  const date = new Date("2026-09-23T15:30:00Z");
  expect(cronMatches("*/15 9-17 * * 1-5", date)).toBeTrue();
  expect(cronMatches("0,15 9-17 * * 1-5", date)).toBeFalse();
  expect(cronMatches("30 15 * * 3", date)).toBeTrue();
  expect(cronMatches("0 8 * * 7", new Date("2026-09-27T08:00:00Z"))).toBeTrue();
});

test("subconscious presets seed once, and removal or edits survive restart", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-presets-")); workspaces.push(workspace);
  const { TaskWorker } = await import("@magi/channel-tasks/worker.js");
  let magi = new Magi("@seed.magi", { workspace });
  let worker = new TaskWorker(magi.bus, { isReady: () => false });
  await worker.start();
  const preset = magi.bus.tasks.list()[0];
  expect(magi.bus.actionItems.list()).toHaveLength(3);
  expect(magi.bus.chats.get(preset.chat_id)?.channel).toBe("subconscious");
  magi.bus.tasks.update(preset.id, { enabled: false, prompt: "User edited review" });
  await worker.start();
  expect(magi.bus.tasks.get(preset.id)?.enabled).toBeFalse();
  expect(magi.bus.tasks.get(preset.id)?.prompt).toBe("User edited review");
  magi.bus.tasks.delete(preset.id);
  magi.bus.actionItems.delete(magi.bus.actionItems.list()[0].id);
  await magi.stop();
  magi = new Magi("@seed.magi", { workspace });
  worker = new TaskWorker(magi.bus, { isReady: () => false });
  await worker.start();
  expect(magi.bus.tasks.list()).toHaveLength(0);
  expect(magi.bus.actionItems.list()).toHaveLength(2);
  expect(magi.bus.chats.list("subconscious")).toHaveLength(1);
  await magi.stop();
});

test("one-time tasks atomically disable and concurrent scanners deduplicate", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-once-")); workspaces.push(workspace);
  const magi = new Magi("@once.magi", { workspace });
  try {
    const chat = magi.bus.chats.forChannel("test-owner", "terminal");
    const once = magi.bus.tasks.create({ name: "once", prompt: "review", cron: "", run_at: "2026-01-01T00:00:00Z", chat_id: chat.id });
    expect(magi.bus.tasks.claimFire(once.id, "2026-01-01T00:00")).toBeTrue();
    expect(magi.bus.tasks.claimFire(once.id, "2026-01-01T00:00")).toBeFalse();
    expect(magi.bus.tasks.claimFire(once.id, "2026-01-01T00:01")).toBeFalse();
    expect(magi.bus.tasks.get(once.id)?.enabled).toBeFalse();
    const recurring = magi.bus.tasks.create({ name: "recurring", prompt: "review", cron: "* * * * *", chat_id: chat.id });
    expect(magi.bus.tasks.claimFire(recurring.id, "2026-01-01T00:00")).toBeTrue();
    expect(magi.bus.tasks.claimFire(recurring.id, "2026-01-01T00:00")).toBeFalse();
    expect(magi.bus.tasks.claimFire(recurring.id, "2026-01-01T00:01")).toBeTrue();
  } finally { await magi.stop(); }
});

test("provider readiness gates private task firing without consuming it", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-ready-")); workspaces.push(workspace);
  const { TaskWorker } = await import("@magi/channel-tasks/worker.js");
  const magi = new Magi("@ready.magi", { workspace });
  try {
    const worker = new TaskWorker(magi.bus, { isReady: () => false });
    await worker.start();
    const task = magi.bus.tasks.list()[0];
    magi.bus.tasks.update(task.id, { cron: "", run_at: "2026-01-01T00:00:00Z" });
    magi.bus.tasks.markFired(task.id, "2020-01-01T00:00");
    await worker.poll();
    expect(magi.bus.tasks.get(task.id)?.last_fired_minute).toBe("2020-01-01T00:00");
    expect(magi.bus.tasks.get(task.id)?.enabled).toBeTrue();
    const readyWorker = new TaskWorker(magi.bus, { isReady: () => true });
    await readyWorker.start(); await readyWorker.poll();
    expect(magi.bus.tasks.get(task.id)?.enabled).toBeFalse();
  } finally { await magi.stop(); }
});

test("private task tools cannot inspect, retarget or change external tasks", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-task-scope-")); workspaces.push(workspace);
  const { TaskWorker } = await import("@magi/channel-tasks/worker.js");
  const magi = new Magi("@scope.magi", { workspace });
  try {
    const worker = new TaskWorker(magi.bus, { isReady: () => false }); await worker.start();
    const internal = magi.bus.chats.forChannel("subconscious", "self");
    const external = magi.bus.chats.forChannel("test-owner", "terminal");
    const task = magi.bus.tasks.create({ name: "external reminder", prompt: "external private text", cron: "0 8 * * *", chat_id: external.id });
    async function call(name: string, args: Record<string, unknown>) {
      const board = magi.bus.board("RunToolJob");
      const id = board.publish({ chat_id: internal.id, call: { tool_call_id: name, name, arguments: args } }, "test");
      await worker.poll(); return board.result(id);
    }
    const listed = await call("list_tasks", { chat_id: external.id });
    expect(listed?.output?.content.includes("external private text")).toBeFalse();
    expect((await call("delete_task", { task_id: task.id }))?.status).toBe("failed");
    expect((await call("update_task", { task_id: task.id, enabled: false }))?.status).toBe("failed");
    expect((await call("schedule_task", { name: task.name, prompt: "distinct private task", frequency: "hourly", target: "subconscious" }))?.status).toBe("completed");
    expect((await call("schedule_task", { name: "loop", prompt: "wake again", frequency: "once", target: "subconscious", run_at: "2026-01-01T00:00:00Z" }))?.status).toBe("failed");
    expect(magi.bus.tasks.get(task.id)?.chat_id).toBe(external.id);
    expect(magi.bus.tasks.get(task.id)?.enabled).toBeTrue();
  } finally { await magi.stop(); }
});

test("task fire rolls back on persistence failure and committed journal recovers after restart", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-task-journal-")); workspaces.push(workspace);
  let magi = new Magi("@journal.magi", { workspace });
  const chat = magi.bus.chats.forChannel("test-owner", "terminal");
  const task = magi.bus.tasks.create({ name: "once journal", prompt: "review", cron: "", run_at: "2026-01-01T00:00:00Z", chat_id: chat.id });
  expect(() => magi.bus.tasks.fire(task.id, "2026-01-01T00:00", () => { throw new Error("disk failure"); })).toThrow("disk failure");
  expect(magi.bus.tasks.get(task.id)?.enabled).toBeTrue();
  expect(magi.bus.tasks.get(task.id)?.last_fired_minute).toBeNull();
  const stored = magi.bus.tasks.fire(task.id, "2026-01-01T00:00", () => magi.bus.deliveries.persist({ chat_id: chat.id, contact_id: SYSTEM_CONTACT_ID, content: "durable wake", publisher: "task" }));
  expect(stored?.delivery.job_id).toBeNull();
  await magi.stop();
  magi = new Magi("@journal.magi", { workspace });
  expect(magi.bus.tasks.get(task.id)?.enabled).toBeFalse();
  expect(magi.bus.deliveries.get(stored!.delivery.message_id)?.job_id).not.toBeNull();
  await magi.stop();
});

test("stopped task workers neither fire nor claim tools", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-task-stop-")); workspaces.push(workspace);
  const { TaskWorker } = await import("@magi/channel-tasks/worker.js");
  const magi = new Magi("@stop.magi", { workspace });
  try {
    const worker = new TaskWorker(magi.bus); await worker.start(); await worker.stop();
    const chat = magi.bus.chats.forChannel("test-owner", "terminal");
    const task = magi.bus.tasks.create({ name: "stopped", prompt: "review", cron: "* * * * *", chat_id: chat.id });
    expect(await worker.poll()).toBeFalse();
    expect(magi.bus.tasks.get(task.id)?.last_fired_minute).toBeNull();
  } finally { await magi.stop(); }
});

test("task identity is its ID, not its label, including foreground edits to private tasks", async () => {
  const { taskTools } = await import("@magi/channel-tasks/tools.js");
  const workspace = await mkdtemp(join(tmpdir(), "magi-task-id-")); workspaces.push(workspace);
  const magi = new Magi("@task-id.magi", { workspace });
  try {
    const inner = magi.bus.chats.forChannel("subconscious", "self");
    const peer = magi.bus.chats.forChannel("peer", "test");
    const tools = taskTools(magi.bus);
    const run = async (name: string, args: Record<string, unknown>) => JSON.parse(await tools.find(tool => tool.name === name)!.run(args));
    const input = { name: "Review", prompt: "original", frequency: "hourly", target: "subconscious" };
    const first = await run("schedule_task", input);
    const second = await run("schedule_task", { ...input, prompt: "second" });
    const third = await run("schedule_task", { ...input, target: "chat", chat_id: peer.id });
    assert.equal(new Set([first.task_id, second.task_id, third.task_id]).size, 3);
    assert.equal(magi.bus.tasks.get(first.task_id)?.prompt, "original");
    assert.equal(magi.bus.tasks.get(second.task_id)?.chat_id, inner.id);
    assert.equal(magi.bus.tasks.get(third.task_id)?.chat_id, peer.id);
    await run("update_task", { task_id: first.task_id, name: "Renamed", prompt: "intentional foreground edit", enabled: false });
    assert.equal(magi.bus.tasks.get(first.task_id)?.id, first.task_id);
    assert.equal(magi.bus.tasks.get(first.task_id)?.prompt, "intentional foreground edit");
    await run("update_task", { task_id: first.task_id, name: "Review" });
    await run("delete_task", { task_id: second.task_id });
    assert.equal(magi.bus.tasks.get(second.task_id), null);
    assert.equal(magi.bus.tasks.get(first.task_id)?.name, "Review");
    assert.equal(magi.bus.tasks.get(third.task_id)?.prompt, "original");
  } finally { await magi.stop(); }
});

test("completed one-time tasks require a future reschedule, not a bare re-enable", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-task-rearm-")); workspaces.push(workspace);
  const magi = new Magi("@task-rearm.magi", { workspace });
  try {
    const chat = magi.bus.chats.forChannel("test-owner", "terminal");
    const input = { name: "once", prompt: "review", cron: "", run_at: "2020-01-01T00:00:00Z", chat_id: chat.id };
    const task = magi.bus.tasks.create(input);
    assert.equal(magi.bus.tasks.claimFire(task.id, "2020-01-01T00:01"), true, "never-fired overdue task can catch up");
    assert.throws(() => magi.bus.tasks.update(task.id, { enabled: true }), /future run_at/);
    assert.notEqual(magi.bus.tasks.create(input).id, task.id, "same label creates a distinct task, not a rearm");
    const future = new Date(Date.now() + 3_600_000).toISOString();
    magi.bus.tasks.update(task.id, { run_at: future });
    assert.equal(magi.bus.tasks.update(task.id, { enabled: true }).run_at, future);
    const overdue = magi.bus.tasks.create({ ...input, name: "never fired", enabled: false });
    assert.equal(magi.bus.tasks.update(overdue.id, { enabled: true }).enabled, true);
  } finally { await magi.stop(); }
});

test("task identity migration preserves every field and only removes name uniqueness", async () => {
  const { DatabaseSync } = await import("node:sqlite");
  const { readFileSync } = await import("node:fs");
  const db = new DatabaseSync(":memory:");
  try {
    const migration = (name: string) => readFileSync(new URL(`../../../../packages/bus/drizzle/books/${name}`, import.meta.url), "utf8");
    db.exec(migration("0000_init.sql"));
    db.exec(migration("0015_task_once.sql"));
    db.exec(`INSERT INTO books_tasks (id, name, prompt, source, enabled, cron, chat_id, last_fired_minute, run_at) VALUES
      (7, 'Recurring', 'keep recurrence', 'user', 1, '17 * * * *', 3, '2026-01-01T03:17', NULL),
      (42, 'Consumed once', 'do not replay', 'subconscious', 0, '', 9, '2026-01-01T03:17', '2026-01-01T03:17:00Z')`);
    const before = db.prepare("SELECT * FROM books_tasks ORDER BY id").all();
    db.exec(migration("0017_task_identity.sql"));
    assert.deepEqual(db.prepare("SELECT * FROM books_tasks ORDER BY id").all(), before);
    db.exec("INSERT INTO books_tasks (name, prompt, cron, chat_id) VALUES ('Consumed once', 'distinct new task', '0 8 * * *', 9)");
    assert.equal(db.prepare("SELECT id FROM books_tasks ORDER BY id DESC LIMIT 1").get()?.id, 43);
    assert.equal(db.prepare("SELECT enabled FROM books_tasks WHERE id = 42").get()?.enabled, 0);
  } finally { db.close(); }
});

test("preset seed identity is independent of task label and survives restart", async () => {
  const { TaskWorker } = await import("@magi/channel-tasks/worker.js");
  const workspace = await mkdtemp(join(tmpdir(), "magi-seed-id-")); workspaces.push(workspace);
  let magi = new Magi("@seed-id.magi", { workspace });
  try {
    const chat = magi.bus.chats.forChannel("test-owner", "terminal");
    const user = magi.bus.tasks.create({ name: "Subconscious review", prompt: "user task", cron: "0 8 * * *", chat_id: chat.id });
    const worker = new TaskWorker(magi.bus, { isReady: () => false });
    await worker.start();
    const preset = magi.bus.tasks.list().find(task => task.source === "subconscious")!;
    assert.notEqual(preset.id, user.id);
    assert.equal(magi.bus.tasks.list().length, 2);
    magi.bus.tasks.update(preset.id, { name: "Renamed preset", enabled: false });
    await worker.start();
    assert.equal(magi.bus.tasks.list().length, 2);
    magi.bus.tasks.delete(preset.id);
    await magi.stop();
    magi = new Magi("@seed-id.magi", { workspace });
    await new TaskWorker(magi.bus, { isReady: () => false }).start();
    assert.deepEqual(magi.bus.tasks.list().map(task => task.id), [user.id]);
    assert.equal(magi.bus.tasks.get(user.id)?.prompt, "user task");
  } finally { await magi.stop(); }
});
