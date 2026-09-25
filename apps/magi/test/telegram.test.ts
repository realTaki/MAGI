import { expect, test , sleep} from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
import { MAGI_CONTACT_ID } from "@magi/bus";
import { jsonServer } from "./httpServer.js";

/** The id the fake `getMe` answers with, so a reply to it is a reply to the MAGI. */
const BOT_USER_ID = 1;

/** A message in the shape Telegram's `getUpdates` returns it. */
function update(input: {
  chatId: number;
  chatType: "private" | "supergroup";
  fromId: number;
  firstName: string;
  text: string;
  replyingToBot?: boolean;
}) {
  return {
    update_id: 1,
    message: {
      message_id: 10,
      date: 1,
      chat: { id: input.chatId, type: input.chatType },
      from: { id: input.fromId, is_bot: false, first_name: input.firstName },
      // A quoted message is parsed like any other, so it carries the chat it was sent in.
      reply_to_message: input.replyingToBot
        ? {
          message_id: 2,
          date: 1,
          chat: { id: input.chatId, type: input.chatType },
          from: { id: BOT_USER_ID, is_bot: true, first_name: "MAGI" },
          text: "earlier",
        }
        : undefined,
      text: input.text,
    },
  };
}

/**
 * Stands in for the Telegram API. The Chat SDK owns the protocol, so this answers only
 * the calls its adapter makes: who am I, drop the webhook, hand over updates, send replies.
 */
async function telegramApi(updates: unknown[]) {
  const posts: Array<Record<string, unknown>> = [];
  let handed = 0;
  const server = await jsonServer(async (request) => {
      const method = request.url.pathname.split("/").pop();
      const body = request.body;
      if (method === "getMe") return { ok: true, result: { id: BOT_USER_ID, is_bot: true, first_name: "MAGI", username: "magi_test_bot" } };
      if (method === "deleteWebhook") return { ok: true, result: true };
      if (method === "getUpdates") {
        // Each update is handed over once; the offset the adapter asks for next is ignored.
        if (handed < updates.length) return { ok: true, result: [updates[handed++]] };
        await sleep(20);
        return { ok: true, result: [] };
      }
      if (method === "sendMessage") {
        posts.push(body);
        return { ok: true, result: { message_id: 2 } };
      }
      return { ok: true, result: {} };
  });
  return { posts, server, handedOut: () => handed };
}

function answeringMagi(workspace: string, base: string) {
  return new Magi("@alice.magi", {
    workspace,
    telegram: { token: "test", apiBase: base },
    client: { async complete() { return { role: "assistant", content: "hello" } as const; } },
  });
}

test("Telegram text reaches Agent and its reply is delivered", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tg-"));
  const api = await telegramApi([update({ chatId: 42, chatType: "private", fromId: 42, firstName: "operator", text: "hi" })]);
  const magi = answeringMagi(workspace, `http://127.0.0.1:${api.server.port}/bottest`);
  try {
    await magi.start();
    for (let i = 0; i < 200 && api.posts.length === 0; i++) await sleep(10);
    expect(api.posts[0]).toEqual({ chat_id: "42", text: "hello" });
    const chat = magi.bus.chats.forChannel("tg", "42");
    expect(magi.bus.messages.list(chat.id).map((message) => message.content)).toEqual(["hi", "hello"]);
    // The speaker is a contact of their own, and a member of the chat.
    const speaker = magi.bus.contacts.forTg("42");
    expect(magi.bus.chatMembers.list(chat.id).map((member) => member.id)).toContain(speaker.id);
  } finally {
    await magi.stop();
    await api.server.close();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("a group message that does not address the MAGI is ignored", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tg-quiet-"));
  const api = await telegramApi([update({ chatId: -100, chatType: "supergroup", fromId: 7, firstName: "guest", text: "just chatting" })]);
  const magi = answeringMagi(workspace, `http://127.0.0.1:${api.server.port}/bottest`);
  try {
    await magi.start();
    // Wait for the update to have been handed over: the point is that nothing follows.
    for (let i = 0; i < 200 && api.handedOut() === 0; i++) await sleep(10);
    expect(api.handedOut()).toBe(1);
    await sleep(200);
    const chat = magi.bus.chats.forChannel("tg", "-100");
    expect(magi.bus.messages.count(chat.id)).toBe(0);
    expect(api.posts).toHaveLength(0);
  } finally {
    await magi.stop();
    await api.server.close();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("a reply to the MAGI in a group counts as a mention", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tg-reply-"));
  const api = await telegramApi([update({ chatId: -100, chatType: "supergroup", fromId: 7, firstName: "guest", text: "and this?", replyingToBot: true })]);
  const magi = answeringMagi(workspace, `http://127.0.0.1:${api.server.port}/bottest`);
  try {
    await magi.start();
    for (let i = 0; i < 200 && api.posts.length === 0; i++) await sleep(10);
    expect(api.posts[0]).toEqual({ chat_id: "-100", text: "hello" });
    const chat = magi.bus.chats.forChannel("tg", "-100");
    const speaker = magi.bus.contacts.forTg("7");
    expect(magi.bus.messages.list(chat.id).map((message) => message.content)).toEqual(["and this?", "hello"]);
    expect(magi.bus.messages.list(chat.id)[0]?.contact_id).toBe(speaker.id);
    const members = magi.bus.chatMembers.list(chat.id).map((member) => member.id);
    expect(members).toContain(speaker.id);
    expect(members).toContain(MAGI_CONTACT_ID);
  } finally {
    await magi.stop();
    await api.server.close();
    await rm(workspace, { recursive: true, force: true });
  }
});
