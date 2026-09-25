import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";

test("Telegram text reaches Agent and its reply is delivered", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tg-"));
  let delivered: Record<string, unknown> | null = null;
  // The one message the operator sends, in the shape Telegram's getUpdates returns it.
  const update = {
    update_id: 1,
    message: {
      message_id: 1,
      date: 1,
      chat: { id: 42, type: "private" },
      from: { id: 42, is_bot: false, first_name: "operator" },
      text: "hi",
    },
  };
  // The Chat SDK owns the protocol, so this fake answers the calls its Telegram adapter
  // makes: who am I, drop the webhook, give me updates, send this reply.
  const server = Bun.serve({
    port: 0,
    async fetch(request) {
      const method = new URL(request.url).pathname.split("/").pop();
      const body = await request.json() as { offset?: number };
      if (method === "getMe") return Response.json({ ok: true, result: { id: 1, is_bot: true, first_name: "MAGI", username: "magi_test_bot" } });
      if (method === "deleteWebhook") return Response.json({ ok: true, result: true });
      if (method === "getUpdates") {
        // Acknowledging an update means asking for the next offset.
        if ((body.offset ?? 0) <= 1) return Response.json({ ok: true, result: [update] });
        await Bun.sleep(20);
        return Response.json({ ok: true, result: [] });
      }
      if (method === "sendMessage") {
        delivered = body;
        return Response.json({ ok: true, result: { message_id: 2 } });
      }
      return Response.json({ ok: true, result: {} });
    },
  });
  const magi = new Magi("@alice.magi", {
    workspace,
    telegram: { token: "test", apiBase: `http://127.0.0.1:${server.port}/bottest` },
    client: { async complete() { return { role: "assistant", content: "hello" } as const; } },
  });
  try {
    await magi.start();
    for (let i = 0; i < 200 && !delivered; i++) await Bun.sleep(10);
    expect(delivered as unknown).toEqual({ chat_id: "42", text: "hello" });
    const conversation = magi.bus.conversations.forChannel("tg", "42");
    expect(magi.bus.messages.list(conversation.id).map((message) => message.content)).toEqual(["hi", "hello"]);
  } finally {
    await magi.stop();
    server.stop(true);
    await rm(workspace, { recursive: true, force: true });
  }
});
