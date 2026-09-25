import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";

/** Stands in for the Telegram API: it answers slowly, the way long polling does. */
function telegramServer(reply: (method: string) => Record<string, unknown>) {
  const calls: string[] = [];
  const server = Bun.serve({
    port: 0,
    async fetch(request) {
      const method = new URL(request.url).pathname.split("/").pop() ?? "";
      calls.push(method);
      if (method === "getUpdates") await Bun.sleep(20);
      return Response.json(reply(method));
    },
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
    await Bun.sleep(10);
  }
  throw new Error(`ManageWorkerNotify ${action} ${worker} did not finish`);
}

function idleClient() {
  return { async complete() { return { role: "assistant", content: "unused" } as const; } };
}

test("a channel whose credentials arrive later comes up without a restart", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-manager-"));
  const telegram = telegramServer(() => ({ ok: true, result: [] }));
  const magi = new Magi("@manager.magi", { workspace, client: idleClient() });
  try {
    await magi.start();
    await Bun.sleep(50);
    expect(telegram.calls).toEqual([]);

    magi.bus.settings.set("telegram.bot_token", "test-token");
    magi.bus.settings.set("telegram.api_base", telegram.base);
    const started = await manage(magi, "tg", "start");
    expect(started.status).toBe("completed");
    expect(started.output?.running).toContain("tg");
    for (let i = 0; i < 200 && !telegram.calls.includes("getUpdates"); i++) await Bun.sleep(10);
    expect(telegram.calls).toContain("getUpdates");

    // Stopping is a decision, not a pause: the token is still there and it stays down.
    await manage(magi, "tg", "stop");
    expect(magi.bus.settings.get("worker.tg.enabled")).toBe("false");
    const settled = telegram.calls.length;
    await Bun.sleep(150);
    expect(telegram.calls.length).toBe(settled);

    const restarted = await manage(magi, "tg", "restart");
    expect(restarted.output?.running).toContain("tg");
    expect(magi.bus.settings.get("worker.tg.enabled")).toBe("true");
    for (let i = 0; i < 200 && telegram.calls.length === settled; i++) await Bun.sleep(10);
    expect(telegram.calls.length).toBeGreaterThan(settled);
  } finally {
    await magi.stop();
    telegram.server.stop(true);
    await rm(workspace, { recursive: true, force: true });
  }
});

test("a channel that fails while it runs reaches the chat the operator used", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-manager-health-"));
  // Starting is fine — the token is accepted — and then polling breaks, which is the
  // failure the worker cannot see for itself: the Chat SDK adapter reports it.
  const telegram = telegramServer((method) => method === "getUpdates"
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
    magi.bus.setHomeChat(chat.id);
    magi.bus.settings.set("telegram.bot_token", "bad-token");
    magi.bus.settings.set("telegram.api_base", telegram.base);
    await manage(magi, "tg", "start");

    for (let i = 0; i < 500 && delivered.length === 0; i++) await Bun.sleep(10);
    expect(delivered).toHaveLength(1);
    expect(delivered[0]).toContain('"tg"');
    expect(delivered[0]).toContain("Telegram polling request failed");
    expect(magi.bus.messages.list(chat.id).at(-1)?.content).toBe(delivered[0]);
  } finally {
    await magi.stop();
    telegram.server.stop(true);
    await rm(workspace, { recursive: true, force: true });
  }
});

test("a channel that cannot log in is reported instead of starting", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-manager-login-"));
  // Every call fails, so the adapter cannot even resolve who the bot is.
  const telegram = telegramServer(() => ({ ok: false, description: "Unauthorized" }));
  const delivered: string[] = [];
  const magi = new Magi("@login.magi", {
    workspace,
    deliver: (text) => delivered.push(text),
    client: idleClient(),
  });
  try {
    await magi.start();
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    magi.bus.setHomeChat(chat.id);
    magi.bus.settings.set("telegram.bot_token", "bad-token");
    magi.bus.settings.set("telegram.api_base", telegram.base);

    const started = await manage(magi, "tg", "start");
    expect(started.status).toBe("completed");
    expect(started.output?.running).not.toContain("tg");
    // The notice travels as a delivery job, so it lands a moment later.
    for (let i = 0; i < 200 && delivered.length === 0; i++) await Bun.sleep(10);
    expect(delivered).toHaveLength(1);
    expect(delivered[0]).toContain('worker "tg" could not start');
    expect(magi.bus.messages.list(chat.id).at(-1)?.content).toBe(delivered[0]);
  } finally {
    await magi.stop();
    telegram.server.stop(true);
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
