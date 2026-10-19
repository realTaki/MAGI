import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  ASP_MAGI_HANDLE,
  EVA_MAGI_HANDLE,
  MAGI_CONTACT_ID,
  magiLocalName,
  SHELL_MAGI_HANDLE,
  messageDelivery,
} from "@magi/bus";
import { Asp } from "../../asp/asp.ts";
import { Magi } from "../../eva/eva.ts";

/*
 * Society chat: operator DMs each host MAGI, and the three MAGIs talk to each
 * other through ASP. Casper is the in-process ASP host; Balthasar is attached
 * the way Shell does (settings + ManageWorkerNotify); Melchior is a Magi with
 * constructor credentials, the spawned-eva shape.
 */

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function fakeClient(handle: string) {
  const name = magiLocalName(handle);
  return {
    async complete(job: { messages: Array<{ role: string; content?: string }> }) {
      const text = [...job.messages].reverse().find((message) => message.role === "user")?.content ?? "";
      if (!text.includes(name)) return { role: "assistant" as const, content: "NO_REPLY" };
      return { role: "assistant" as const, content: `ok:${name}` };
    },
    async verify() { throw new Error("no provider in tests"); },
    configure() {},
  };
}

async function request(
  origin: string,
  method: string,
  pathname: string,
  options: { token?: string; body?: unknown } = {},
): Promise<{ status: number; data: unknown }> {
  const response = await fetch(new URL(pathname, origin), {
    method,
    headers: {
      ...(options.token === undefined ? {} : { authorization: `Bearer ${options.token}` }),
      ...(options.body === undefined ? {} : { "content-type": "application/json" }),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  return { status: response.status, data: text === "" ? null : JSON.parse(text) };
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("expected an object");
  }
  return value as Record<string, unknown>;
}

async function until(label: string, check: () => Promise<boolean> | boolean, ms = 8_000): Promise<void> {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (await check()) return;
    await sleep(20);
  }
  throw new Error(`timed out waiting for ${label}`);
}

async function notify(magi: Magi, action: "start" | "stop" | "restart"): Promise<void> {
  const board = magi.bus.board("ManageWorkerNotify");
  const id = board.publish({ worker: "asp", action }, "test");
  await until(`ManageWorkerNotify ${action} asp`, () => board.result(id) !== undefined, 5_000);
}

async function online(origin: string, token: string, handle: string): Promise<boolean> {
  const listed = record((await request(origin, "GET", "/agents", { token })).data).agents;
  if (!Array.isArray(listed)) return false;
  const row = listed.find((agent) => record(agent).handle === handle);
  return record(row ?? {}).online === true;
}

async function joined(origin: string, token: string, chatId: string, handle: string): Promise<boolean> {
  const view = record((await request(origin, "GET", `/chats/${chatId}`, { token })).data);
  const participants = view.participants;
  if (!Array.isArray(participants)) return false;
  return participants.some((row) => record(row).handle === handle && record(row).status === "joined");
}

async function waitForContent(
  origin: string,
  token: string,
  chatId: string,
  sender: string,
  content: string,
): Promise<void> {
  await until(`${sender} said ${content}`, async () => {
    const events = record((await request(origin, "GET", `/chats/${chatId}/events`, { token })).data).events;
    if (!Array.isArray(events)) return false;
    return events.some((event) => {
      const row = record(event);
      if (row.type !== "chat.message") return false;
      const payload = record(row.payload);
      return payload.sender === sender && payload.content === content;
    });
  });
}

test("operator DMs each MAGI and MAGIs deliver to each other through ASP", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "society-chat-"));
  t.after(() => rm(root, { recursive: true, force: true }));

  const asp = new Asp({
    databasePath: path.join(root, "asp.sqlite"),
    workspace: path.join(root, "asp"),
    host: "127.0.0.1",
    port: 0,
    client: fakeClient(ASP_MAGI_HANDLE),
  });
  await asp.start();
  t.after(() => asp.stop());
  const origin = asp.origin;
  const token = asp.operatorToken;
  await until("Casper online", () => asp.transport.isOnline(ASP_MAGI_HANDLE));

  const bot = async (handle: string) => {
    const created = record((await request(origin, "POST", "/chats", { token, body: { kind: "bot", handle } })).data);
    const magi = record(created.magi);
    assert.equal(created.kind, "bot");
    assert.equal(typeof created.chat_id, "string");
    assert.equal(typeof magi.token, "string");
    return { chatId: String(created.chat_id), magiToken: String(magi.token) };
  };
  const balthasar = await bot(SHELL_MAGI_HANDLE);
  const casper = await bot(ASP_MAGI_HANDLE);
  const melchior = await bot(EVA_MAGI_HANDLE);

  const shellMagi = new Magi(SHELL_MAGI_HANDLE, {
    workspace: path.join(root, magiLocalName(SHELL_MAGI_HANDLE)),
    client: fakeClient(SHELL_MAGI_HANDLE),
  });
  const evaMagi = new Magi(EVA_MAGI_HANDLE, {
    workspace: path.join(root, magiLocalName(EVA_MAGI_HANDLE)),
    asp: { base: origin, token: melchior.magiToken },
    client: fakeClient(EVA_MAGI_HANDLE),
  });
  await shellMagi.start();
  t.after(() => shellMagi.stop());
  shellMagi.bus.settings.set("asp.base", origin);
  shellMagi.bus.settings.set("asp.token", balthasar.magiToken);
  await notify(shellMagi, "start");
  await evaMagi.start();
  t.after(() => evaMagi.stop());

  await until("Balthasar online", () => online(origin, token, SHELL_MAGI_HANDLE));
  await until("Melchior online", () => online(origin, token, EVA_MAGI_HANDLE));
  await until("Balthasar joined 1:1", () => joined(origin, token, balthasar.chatId, SHELL_MAGI_HANDLE));
  await until("Casper joined 1:1", () => joined(origin, token, casper.chatId, ASP_MAGI_HANDLE));
  await until("Melchior joined 1:1", () => joined(origin, token, melchior.chatId, EVA_MAGI_HANDLE));

  assert.equal((await request(origin, "POST", `/chats/${balthasar.chatId}/messages`, {
    token, body: { content: `${SHELL_MAGI_HANDLE} ping` },
  })).status, 201);
  assert.equal((await request(origin, "POST", `/chats/${casper.chatId}/messages`, {
    token, body: { content: `${ASP_MAGI_HANDLE} ping` },
  })).status, 201);
  assert.equal((await request(origin, "POST", `/chats/${melchior.chatId}/messages`, {
    token, body: { content: `${EVA_MAGI_HANDLE} ping` },
  })).status, 201);

  await waitForContent(origin, token, balthasar.chatId, SHELL_MAGI_HANDLE, `ok:${magiLocalName(SHELL_MAGI_HANDLE)}`);
  await waitForContent(origin, token, casper.chatId, ASP_MAGI_HANDLE, `ok:${magiLocalName(ASP_MAGI_HANDLE)}`);
  await waitForContent(origin, token, melchior.chatId, EVA_MAGI_HANDLE, `ok:${magiLocalName(EVA_MAGI_HANDLE)}`);

  const group = record((await request(origin, "POST", "/chats", { token, body: { kind: "group" } })).data);
  const groupId = String(group.chat_id);
  for (const handle of [SHELL_MAGI_HANDLE, ASP_MAGI_HANDLE, EVA_MAGI_HANDLE]) {
    assert.equal((await request(origin, "POST", `/chats/${groupId}/members`, { token, body: { handle } })).status, 200);
  }
  await until("Balthasar joined group", () => joined(origin, token, groupId, SHELL_MAGI_HANDLE));
  await until("Casper joined group", () => joined(origin, token, groupId, ASP_MAGI_HANDLE));
  await until("Melchior joined group", () => joined(origin, token, groupId, EVA_MAGI_HANDLE));

  assert.equal((await request(origin, "POST", `/chats/${groupId}/messages`, {
    token, body: { content: `${EVA_MAGI_HANDLE} group ping` },
  })).status, 201);
  await waitForContent(origin, token, groupId, EVA_MAGI_HANDLE, `ok:${magiLocalName(EVA_MAGI_HANDLE)}`);

  const casperChat = asp.bus.chats.forChannel("asp", groupId);
  messageDelivery.send(asp.bus, casperChat.id, `${SHELL_MAGI_HANDLE} from casper`, MAGI_CONTACT_ID, "asp");
  await waitForContent(origin, token, groupId, SHELL_MAGI_HANDLE, `ok:${magiLocalName(SHELL_MAGI_HANDLE)}`);

  const balthasarChat = shellMagi.bus.chats.forChannel("asp", groupId);
  messageDelivery.send(shellMagi.bus, balthasarChat.id, `${EVA_MAGI_HANDLE} from balthasar`, MAGI_CONTACT_ID, "asp");
  await waitForContent(origin, token, groupId, EVA_MAGI_HANDLE, `ok:${magiLocalName(EVA_MAGI_HANDLE)}`);
});
