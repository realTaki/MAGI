import { expect, test , sleep} from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../eva.js";
import assert from "node:assert/strict";
import { Bus, messageDelivery } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";
import { jsonServer } from "./httpServer.js";

/** Stands in for the Telegram API: it answers slowly, the way long polling does. */
async function telegramServer(reply: (method: string) => Record<string, unknown>) {
  const calls: string[] = [];
  const server = await jsonServer(async (request) => {
      const method = request.url.pathname.split("/").pop() ?? "";
      calls.push(method);
      if (method === "getUpdates") await sleep(20);
      return reply(method);
  });
  return { calls, server, base: `http://127.0.0.1:${server.port}/bottest` };
}

async function manage(magi: Magi, worker: string, action: "start" | "stop" | "restart") {
  const board = magi.bus.board("ManageWorkerNotify");
  const id = board.publish({ worker, action }, "test");
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    const result = board.result(id);
    if (result) return result;
    await sleep(10);
  }
  throw new Error(`ManageWorkerNotify ${action} ${worker} did not finish`);
}

function idleClient() {
  return { async complete() { return { role: "assistant", content: "unused" } as const; } };
}

test("a channel whose credentials arrive later comes up without a restart", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-manager-"));
  const telegram = await telegramServer(() => ({ ok: true, result: [] }));
  const dispatched: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.startsWith(telegram.base)) dispatched.push(new URL(url).pathname.split("/").pop() ?? "");
    return originalFetch(input, init);
  };
  const magi = new Magi("@manager.magi", { workspace, client: idleClient() });
  try {
    await magi.start();
    await sleep(50);
    expect(telegram.calls).toEqual([]);

    magi.bus.secrets.set("telegram.bot_token", "test-token");
    magi.bus.settings.set("telegram.api_base", telegram.base);
    const started = await manage(magi, "tg", "start");
    expect(started.status).toBe("completed");
    expect(started.output?.running).toContain("tg");
    for (let i = 0; i < 200 && !telegram.calls.includes("getUpdates"); i++) await sleep(10);
    expect(telegram.calls).toContain("getUpdates");

    // Stopping is a decision, not a pause: the token is still there and it stays down.
    await manage(magi, "tg", "stop");
    expect(magi.bus.settings.get("worker.tg.enabled")).toBe("false");
    // Count client dispatch, not arrival at the HTTP server: an already-aborted
    // in-flight request may reach that server after stop finishes on Windows.
    const settled = dispatched.length;
    await sleep(150);
    expect(dispatched.length).toBe(settled);

    const restarted = await manage(magi, "tg", "restart");
    expect(restarted.output?.running).toContain("tg");
    expect(magi.bus.settings.get("worker.tg.enabled")).toBe("true");
    for (let i = 0; i < 200 && !dispatched.slice(settled).includes("getUpdates"); i++) await sleep(10);
    expect(dispatched.slice(settled)).toContain("getUpdates");
  } finally {
    try { await magi.stop(); } finally { globalThis.fetch = originalFetch; }
    await telegram.server.close();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("a channel that fails while it runs reaches the chat the operator used", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-manager-health-"));
  // Starting is fine — the token is accepted — and then polling breaks, which is the
  // failure the worker cannot see for itself: the Chat SDK adapter reports it.
  const telegram = await telegramServer((method) => method === "getUpdates"
    ? { ok: false, description: "Unauthorized" }
    : { ok: true, result: {} });
  const delivered: string[] = [];
  const magi = new Magi("@health.magi", {
    workspace,
    deliver: (text) => delivered.push(text),
    client: idleClient(),
  });
  try {
    await magi.start();
    // Speaking once is what gives the workspace an address to report trouble to.
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    messageDelivery.setHomeChat(magi.bus, chat.id);
    magi.bus.secrets.set("telegram.bot_token", "bad-token");
    magi.bus.settings.set("telegram.api_base", telegram.base);
    await manage(magi, "tg", "start");

    for (let i = 0; i < 500 && !magi.bus.messages.list(chat.id).some(message => message.content.includes("Telegram polling request failed")); i++) await sleep(10);
    const diagnostic = magi.bus.messages.list(chat.id).find(message => message.content.includes("Telegram polling request failed"))!;
    expect(diagnostic.contact_id).toBe(1);
    expect(diagnostic.content).toContain('"tg"');
    expect(delivered.includes(diagnostic.content)).toBeFalse();
  } finally {
    await magi.stop();
    await telegram.server.close();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("a channel that cannot log in is reported instead of starting", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-manager-login-"));
  // Every call fails, so the adapter cannot even resolve who the bot is.
  const telegram = await telegramServer(() => ({ ok: false, description: "Unauthorized" }));
  const delivered: string[] = [];
  const magi = new Magi("@login.magi", {
    workspace,
    deliver: (text) => delivered.push(text),
    client: idleClient(),
  });
  try {
    await magi.start();
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    messageDelivery.setHomeChat(magi.bus, chat.id);
    magi.bus.secrets.set("telegram.bot_token", "bad-token");
    magi.bus.settings.set("telegram.api_base", telegram.base);

    const started = await manage(magi, "tg", "start");
    expect(started.status).toBe("completed");
    expect(started.output?.running).not.toContain("tg");
    const diagnostic = magi.bus.messages.list(chat.id).find(message => message.content.includes('worker "tg" could not start'))!;
    expect(diagnostic.contact_id).toBe(1);
    expect(delivered.includes(diagnostic.content)).toBeFalse();
  } finally {
    await magi.stop();
    await telegram.server.close();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("an unknown worker name is refused", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-manager-unknown-"));
  const magi = new Magi("@unknown.magi", { workspace, client: idleClient() });
  try {
    await magi.start();
    const result = await manage(magi, "nope", "start");
    expect(result.status).toBe("failed");
    expect(result.error).toContain("unknown worker");
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("shutdown retires an old poll loop when a concurrent request restarts the same worker", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-manager-drain-"));
  const bus = new Bus("@drain.magi", workspace);
  let starts = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let first = true;
  const worker = {
    worker_name: "drainer",
    start() { starts++; },
    async poll() { if (first) { first = false; await held; } return false; },
  };
  const manager = new ManagerWorker(bus, [worker], { drain: ["drainer"], build: { app: "eva", script: "build" } });
  let stopping: Promise<void> | undefined;
  try {
    await manager.start();
    stopping = manager.stop(); // Removes the old generation while its poll is held.
    bus.board("ManageWorkerNotify").publish({ worker: "drainer", action: "start" }, "test");
    const deadline = Date.now() + 3000;
    while (starts < 2 && Date.now() < deadline) await sleep(10);
    assert.equal(starts, 2, "the supervisor processed the concurrent start");
    release();
    let closed = false;
    void stopping.then(() => { closed = true; });
    const stopDeadline = Date.now() + 1000;
    while (!closed && Date.now() < stopDeadline) await sleep(10);
    assert.equal(closed, true, "an old loop must not adopt the new worker generation");
  } finally {
    release();
    // Also release both loops when demonstrating the pre-fix failure.
    bus.settings.set("worker.drainer.enabled", "false");
    bus.board("ManageWorkerNotify").publish({ worker: "drainer", action: "stop" }, "test");
    await (stopping ?? manager.stop());
    bus.close();
    await rm(workspace, { recursive: true, force: true });
  }
});
