import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";

test("Telegram text reaches Agent and its reply is delivered", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "ts-magi-tg-"));
  let delivered: Record<string, unknown> | null = null;
  const server = Bun.serve({
    port: 0,
    async fetch(request) {
      const url = new URL(request.url);
      const body = await request.json() as { offset?: number };
      if (url.pathname.endsWith("/getUpdates")) {
        if (body.offset === 0) return Response.json({ ok: true, result: [{ update_id: 1, message: { chat: { id: 42 }, text: "hi" } }] });
        await Bun.sleep(20);
        return Response.json({ ok: true, result: [] });
      }
      delivered = body;
      return Response.json({ ok: true, result: {} });
    },
  });
  const magi = new Magi("@alice.magi", {
    workspace,
    telegram: { token: "test", apiBase: `http://127.0.0.1:${server.port}/bottest` },
    client: { async complete() { return { role: "assistant", content: "hello" } as const; } },
  });
  try {
    magi.start();
    for (let i = 0; i < 200 && !delivered; i++) await Bun.sleep(10);
    expect(delivered as unknown).toEqual({ chat_id: 42, text: "hello" });
    const conversation = magi.bus.conversations.forChannel("tg", "42");
    expect(magi.bus.messages.list(conversation.id).map((message) => message.content)).toEqual(["hi", "hello"]);
  } finally {
    await magi.stop();
    server.stop(true);
    await rm(workspace, { recursive: true, force: true });
  }
});
