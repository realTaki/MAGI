import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import {
  ASP_MAGI_HANDLE,
  ASP_MAGI_NICKNAME,
  magiNodeName,
  sequentialMagiHandle,
  SHELL_MAGI_HANDLE,
  USER_HANDLE,
} from "@magi/bus";
import { Asp } from "../asp.js";
import { connect, isRecord, receiveInvite, request, tempRoot, withApp } from "./helpers.js";

/*
 * Business flow: creating a chat (`docs/architecture.md`, "Relay").
 * `POST /chats {kind:"bot"}` assigns the next `eva-NNN` and starts it;
 * `{kind:"group"}` opens with the operator alone; an invite arrives as
 * `chat.invited`, and the MAGI joins on receipt.
 */

function databasePath(root: string): string {
  return path.join(root, "asp.sqlite");
}

test("new bot registers a magi and hands its runner the credential", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const created = await request(app, "POST", "/chats", { token, body: { kind: "bot" } });
    assert.equal(created.status, 201);
    const body = record(created.data);
    assert.equal(body.kind, "bot");
    assert.equal(typeof body.chat_id, "string");
    assert.equal(body.name, magiNodeName(0));
    assert.deepEqual(body.agents, [sequentialMagiHandle(0)]);
    const magi = record(body.magi);
    assert.equal(magi.name, magiNodeName(0));
    assert.equal(magi.handle, sequentialMagiHandle(0));
    assert.equal(typeof magi.token, "string");
    // ASP registers the agent; starting it belongs to the desktop app.
    assert.equal("spawned" in body, false);
    const roster = recordList(record((await request(app, "GET", "/agents", { token })).data).agents);
    assert.deepEqual(
      roster.filter((agent) => agent.managed === true && agent.handle !== ASP_MAGI_HANDLE),
      [
        {
          handle: sequentialMagiHandle(0),
          token: magi.token,
          name: magiNodeName(0),
          nickname: null,
          managed: true,
          online: false,
        },
      ],
    );
    const listed = record(await request(app, "GET", "/chats", { token }).then((response) => response.data));
    const chats = listed.chats;
    assert.ok(Array.isArray(chats));
    // Casper may still be opening the operator 1:1 after it comes online, so
    // that room can sort ahead of this bot chat. Look the created id up.
    const listedBot = recordList(chats).find((chat) => chat.chat_id === body.chat_id);
    assert.ok(listedBot, "created bot chat is missing from GET /chats");
    assert.deepEqual(listedBot.agents, body.agents);
  });
});

test("magi credentials cannot use the operator api", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const created = record(
      (await request(app, "POST", "/chats", { token, body: { kind: "bot" } })).data,
    );
    const magiToken = record(created.magi).token;
    if (typeof magiToken !== "string") {
      throw new Error("magi token missing");
    }
    assert.equal((await request(app, "GET", "/chats", { token: magiToken })).status, 403);
    assert.equal((await request(app, "GET", "/bots", { token: magiToken })).status, 403);
    assert.equal((await request(app, "GET", "/agents", { token: magiToken })).status, 403);
    assert.equal((await request(app, "GET", "/agents")).status, 401);
    assert.equal(
      (await request(app, "POST", "/chats", { token: magiToken, body: { kind: "group" } })).status,
      403,
    );
    assert.equal(
      (await request(app, "GET", `/chats/${String(created.chat_id)}`, { token: magiToken })).status,
      200,
    );
  });
});

test("new group opens immediately with no MAGI", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const created = await request(app, "POST", "/chats", { token, body: { kind: "group" } });
    assert.equal(created.status, 201);
    const body = record(created.data);
    assert.equal(body.kind, "group");
    assert.deepEqual(body.agents, []);
    assert.equal("name" in body, false);
    assert.equal("magi" in body, false);
    assert.deepEqual(
      recordList(record((await request(app, "GET", "/agents", { token })).data).agents).filter(
        (agent) => agent.managed === true && agent.handle !== ASP_MAGI_HANDLE,
      ),
      [],
    );
    const patched = await request(app, "PATCH", `/chats/${String(body.chat_id)}`, {
      token,
      body: { topic: "offsite", description: "week of the 14th" },
    });
    assert.equal(patched.status, 200);
    const view = record(patched.data);
    assert.equal(view.topic, "offsite");
    assert.equal(view.description, "week of the 14th");
  });
});

test("create chat rejects a missing kind and ignores a requested name", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const missing = await request(app, "POST", "/chats", { token, body: {} });
    assert.equal(missing.status, 422);
    const extra = await request(app, "POST", "/chats", {
      token,
      body: { kind: "bot", name: "please do not ask", model: "gpt" },
    });
    assert.equal(extra.status, 201);
    const body = record(extra.data);
    assert.equal(body.kind, "bot");
    assert.equal(body.name, magiNodeName(0));
    assert.deepEqual(body.agents, [sequentialMagiHandle(0)]);
  });
});

test("bots lists spawned magi not a static roster", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const seeded = await request(app, "GET", "/bots", { token });
    assert.equal(seeded.status, 200);
    assert.deepEqual(seeded.data, {
      bots: [{ handle: ASP_MAGI_HANDLE, name: ASP_MAGI_NICKNAME, online: true }],
    });
    const first = await request(app, "POST", "/chats", { token, body: { kind: "bot" } });
    const second = await request(app, "POST", "/chats", { token, body: { kind: "bot" } });
    assert.equal(first.status, 201);
    assert.equal(second.status, 201);
    const firstBody = record(first.data);
    const secondBody = record(second.data);
    assert.equal(firstBody.name, magiNodeName(0));
    assert.equal(secondBody.name, magiNodeName(1));
    assert.deepEqual(firstBody.agents, [sequentialMagiHandle(0)]);
    assert.deepEqual(secondBody.agents, [sequentialMagiHandle(1)]);
    const listed = record((await request(app, "GET", "/bots", { token })).data).bots;
    assert.ok(Array.isArray(listed));
    assert.deepEqual(
      new Set(listed.map((row) => record(row).handle)),
      new Set([sequentialMagiHandle(0), ASP_MAGI_HANDLE, sequentialMagiHandle(1)]),
    );
    assert.deepEqual(
      new Set(listed.map((row) => record(row).name)),
      new Set([magiNodeName(0), ASP_MAGI_NICKNAME, magiNodeName(1)]),
    );
    const byHandle = Object.fromEntries(listed.map((row) => [record(row).handle, record(row)]));
    assert.equal(byHandle[sequentialMagiHandle(0)]?.online, false);
    assert.equal(byHandle[ASP_MAGI_HANDLE]?.online, true);
    assert.equal(byHandle[sequentialMagiHandle(1)]?.online, false);
    assert.equal(listed.some((row) => record(row).handle === "@user.magi"), false);
    assert.equal((await request(app, "GET", "/bots")).status, 401);
  });
});

test("group can add a listed bot", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const bot = record((await request(app, "POST", "/chats", { token, body: { kind: "bot" } })).data);
    const group = record((await request(app, "POST", "/chats", { token, body: { kind: "group" } })).data);
    const handle = strings(bot.agents)[0] ?? "";
    const groupId = String(group.chat_id);
    const picker = await request(app, "GET", `/bots?chat_id=${groupId}`, { token });
    assert.equal(picker.status, 200);
    assert.deepEqual(record(picker.data).bots, [
      { handle: ASP_MAGI_HANDLE, name: ASP_MAGI_NICKNAME, online: true, in_chat: false },
      { handle, name: magiNodeName(0), online: false, in_chat: false },
    ]);
    const added = await request(app, "POST", `/chats/${groupId}/members`, {
      token,
      body: { handle },
    });
    assert.equal(added.status, 200);
    const addedBody = record(added.data);
    assert.deepEqual(addedBody.agents, [handle]);
    assert.equal(addedBody.kind, "group");
    const after = await request(app, "GET", `/bots?chat_id=${groupId}`, { token });
    assert.deepEqual(record(after.data).bots, [
      { handle: ASP_MAGI_HANDLE, name: ASP_MAGI_NICKNAME, online: true, in_chat: false },
      { handle, name: magiNodeName(0), online: false, in_chat: true },
    ]);
    const unknown = await request(app, "POST", `/chats/${groupId}/members`, {
      token,
      body: { handle: "@nobody.magi" },
    });
    assert.equal(unknown.status, 404);
  });
});

test("intranet invite marks the payload", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const bot = record((await request(app, "POST", "/chats", { token, body: { kind: "bot" } })).data);
    const events = recordList(
      record((await request(app, "GET", `/chats/${String(bot.chat_id)}/events`, { token })).data).events,
    );
    const invited = events.filter((event) => event.type === "chat.invited");
    assert.ok(invited.length > 0);
    const payload = record(invited[0]?.payload);
    assert.equal(payload.intranet, true);
    assert.equal(payload.invitee, strings(bot.agents)[0]);
  });
});

test("magi joins the group when it receives the invite", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const bot = record((await request(app, "POST", "/chats", { token, body: { kind: "bot" } })).data);
    const group = record((await request(app, "POST", "/chats", { token, body: { kind: "group" } })).data);
    const handle = strings(bot.agents)[0] ?? "";
    const magiToken = String(record(bot.magi).token);
    const groupId = String(group.chat_id);
    const live = await connect(app, magiToken);
    t.after(() => live.socket.close());
    const online = record((await request(app, "GET", `/bots?chat_id=${groupId}`, { token })).data).bots;
    assert.deepEqual(online, [
      { handle: ASP_MAGI_HANDLE, name: ASP_MAGI_NICKNAME, online: true, in_chat: false },
      { handle, name: bot.name, online: true, in_chat: false },
    ]);
    const added = await request(app, "POST", `/chats/${groupId}/members`, { token, body: { handle } });
    assert.equal(added.status, 200);
    const event = await receiveInvite(live.next, groupId, handle);
    assert.equal(record(event.payload).intranet, true);
    const joined = await request(app, "POST", `/chats/${groupId}/join`, { token: magiToken });
    assert.equal(joined.status, 200);
    live.socket.close();
    const view = record((await request(app, "GET", `/chats/${groupId}`, { token })).data);
    assert.deepEqual(view.agents, [handle]);
    assert.equal(participantStatus(view, handle), "joined");
  });
});

test("opening an owner 1:1 reuses the operator bot chat", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const created = record((await request(app, "POST", "/chats", { token, body: { kind: "bot" } })).data);
    const magi = record(created.magi);
    const magiToken = String(magi.token);
    const opened = record((await request(app, "POST", "/chats/open", {
      token: magiToken,
      body: { invite: [USER_HANDLE] },
    })).data);
    const again = record((await request(app, "POST", "/chats/open", {
      token: magiToken,
      body: { invite: [USER_HANDLE] },
    })).data);
    assert.equal(opened.chat_id, created.chat_id);
    assert.equal(again.chat_id, created.chat_id);
    assert.equal(ownerAgentChats(app, String(magi.handle)).length, 1);
  });
});

test("Casper restart and open_owner_chat keep a single owner 1:1", async (t) => {
  const databasePath = path.join(tempRoot(t), "asp.sqlite");
  const workspace = path.join(path.dirname(databasePath), "casper");
  let firstId = "";
  await withApp({ databasePath, workspace }, async (app) => {
    const chat = await waitForOwnerAgentChat(app, ASP_MAGI_HANDLE);
    const before = ownerAgentChats(app, ASP_MAGI_HANDLE).length;
    assert.equal(before, 1);
    const job = app.bus.board("AspCommandJob").publish({ name: "open_owner_chat" }, "test");
    await until("open_owner_chat finished", () => app.bus.board("AspCommandJob").result(job) !== null);
    const result = app.bus.board("AspCommandJob").result(job);
    assert.equal(result?.status, "completed");
    assert.equal(ownerAgentChats(app, ASP_MAGI_HANDLE).length, 1);
    const operator = record((await request(app, "POST", "/chats", {
      token: app.operatorToken,
      body: { kind: "bot", handle: ASP_MAGI_HANDLE },
    })).data);
    assert.equal(operator.chat_id, chat.id);
    assert.equal(ownerAgentChats(app, ASP_MAGI_HANDLE).length, 1);
    firstId = chat.id;
  });
  await withApp({ databasePath, workspace }, async (app) => {
    const chat = await waitForOwnerAgentChat(app, ASP_MAGI_HANDLE);
    assert.equal(chat.id, firstId);
    assert.equal(ownerAgentChats(app, ASP_MAGI_HANDLE).length, 1);
    const job = app.bus.board("AspCommandJob").publish({ name: "open_owner_chat" }, "test");
    await until("open_owner_chat after restart", () => app.bus.board("AspCommandJob").result(job) !== null);
    assert.equal(app.bus.board("AspCommandJob").result(job)?.status, "completed");
    assert.equal(ownerAgentChats(app, ASP_MAGI_HANDLE).length, 1);
  });
});

test("Shell owner API, concurrent recovery and restart preserve the same room and old histories", async (t) => {
  const root = tempRoot(t);
  const options = { databasePath: databasePath(root), workspace: path.join(root, "casper") };
  let firstId: string;
  let extraId: string;
  async function open(app: Asp) {
    return record((await request(app, "POST", "/chats", {
      token: app.operatorToken, body: { kind: "bot", handle: SHELL_MAGI_HANDLE },
    })).data);
  }
  await withApp(options, async (app) => {
    const first = await open(app);
    firstId = String(first.chat_id);
    const agentToken = String(record(first.magi).token);
    async function recover() {
      return Promise.all(Array.from({ length: 4 }, () => request(app, "POST", "/chats/open", {
        token: agentToken, body: { invite: [USER_HANDLE] },
      })));
    }
    assert.ok((await recover()).every((reply) => record(reply.data).chat_id === firstId));
    assert.equal(ownerAgentChats(app, SHELL_MAGI_HANDLE).length, 1, "fresh startup has one room");
    // Model the pre-#39 duplicate without weakening the current creation API.
    const extra = app.store.createChat({ creator: SHELL_MAGI_HANDLE, kind: "bot", topic: null });
    extraId = extra.id;
    app.store.addParticipant(extra.id, SHELL_MAGI_HANDLE, "joined");
    app.store.addParticipant(extra.id, USER_HANDLE, "joined");
    app.store.appendChatEvent(extra.id, "chat.invited", { invitee: USER_HANDLE, by: SHELL_MAGI_HANDLE });
    app.store.appendChatEvent(extra.id, "chat.joined", { agent: USER_HANDLE });
    const sent = await request(app, "POST", `/chats/${extra.id}/messages`, {
      token: agentToken, body: { content: "historical provider card" },
    });
    assert.equal(sent.status, 201);
    const opened = await recover();
    assert.ok(opened.every((reply) => reply.status === 201 && record(reply.data).chat_id === firstId));
    assert.equal((await open(app)).chat_id, firstId);
    assert.equal(ownerAgentChats(app, SHELL_MAGI_HANDLE).length, 2);
  });
  await withApp(options, async (app) => {
    assert.equal((await open(app)).chat_id, firstId);
    assert.equal(ownerAgentChats(app, SHELL_MAGI_HANDLE).length, 2);
    const history = await request(app, "GET", `/chats/${extraId}/events`, { token: app.operatorToken });
    assert.equal(history.status, 200);
    assert.ok(recordList(record(history.data).events).some((event) => record(event.payload).content === "historical provider card"));
  });
});

test("two MAGIs can share a nickname with distinct identities", async (t) => {
  const root = tempRoot(t);
  const options = { databasePath: databasePath(root), workspace: path.join(root, "casper") };
  const other = sequentialMagiHandle(1);
  let oldId: unknown;
  async function openNamed(app: Asp, handle: string) {
    const result = await request(app, "POST", "/chats", {
      token: app.operatorToken, body: { kind: "bot", handle },
    });
    assert.equal(result.status, 201);
    // Seed an already-named identity; no Shell process runs in this relay test.
    const agent = app.store.getAgent(handle);
    assert.ok(agent);
    agent.nickname = "BALTHASAR";
    app.store.updateAgent(agent);
    return record(result.data).chat_id;
  }
  await withApp(options, async (app) => { oldId = await openNamed(app, other); });
  await withApp(options, async (app) => {
    const newId = await openNamed(app, SHELL_MAGI_HANDLE);
    assert.notEqual(newId, oldId);
    assert.equal(await openNamed(app, SHELL_MAGI_HANDLE), newId, "opening the new handle again is idempotent");
    const listed = await request(app, "GET", "/chats", { token: app.operatorToken });
    const balthasars = recordList(record(listed.data).chats).filter((chat) => chat.name === "BALTHASAR");
    assert.equal(balthasars.length, 2);
    assert.deepEqual(new Set(balthasars.flatMap((chat) => chat.agents as string[])), new Set([other, SHELL_MAGI_HANDLE]));
    assert.ok(balthasars.some((chat) => chat.chat_id === oldId));
    assert.ok(app.store.getAgent(other));
    assert.ok(app.store.getAgent(SHELL_MAGI_HANDLE));
  });
});

test("a one-shot owner chat does not reuse or end the 1:1", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const created = record((await request(app, "POST", "/chats", { token, body: { kind: "bot" } })).data);
    const magiToken = String(record(created.magi).token);
    const shot = record((await request(app, "POST", "/chats/open", {
      token: magiToken,
      body: {
        invite: [USER_HANDLE],
        end_after_send: true,
        initial_message: { content: "bye" },
      },
    })).data);
    assert.notEqual(shot.chat_id, created.chat_id);
    const lasting = record((await request(app, "GET", `/chats/${String(created.chat_id)}`, { token })).data);
    assert.equal(lasting.state, "active");
    const ended = record((await request(app, "GET", `/chats/${String(shot.chat_id)}`, { token })).data);
    assert.equal(ended.state, "ended");
  });
});

test("offline magi joins when it connects after the invite", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const bot = record((await request(app, "POST", "/chats", { token, body: { kind: "bot" } })).data);
    const group = record((await request(app, "POST", "/chats", { token, body: { kind: "group" } })).data);
    const handle = strings(bot.agents)[0] ?? "";
    const magiToken = String(record(bot.magi).token);
    const groupId = String(group.chat_id);
    const added = await request(app, "POST", `/chats/${groupId}/members`, { token, body: { handle } });
    assert.equal(added.status, 200);
    assert.equal(participantStatus(record(added.data), handle), "invited");
    const live = await connect(app, magiToken);
    t.after(() => live.socket.close());
    const event = await receiveInvite(live.next, groupId, handle);
    assert.equal(record(event.payload).intranet, true);
    const joined = await request(app, "POST", `/chats/${groupId}/join`, { token: magiToken });
    assert.equal(joined.status, 200);
    live.socket.close();
    const view = record((await request(app, "GET", `/chats/${groupId}`, { token })).data);
    assert.equal(participantStatus(view, handle), "joined");
  });
});

function operatorToken(response: { data: unknown }): string {
  const token = record(response.data).token;
  if (typeof token !== "string") {
    throw new Error("operator token missing");
  }
  return token;
}

function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error("expected an object");
  }
  return value;
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error("expected a list of strings");
  }
  return value;
}

function recordList(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    throw new Error("expected a list");
  }
  return value.map((item) => record(item));
}

function participantStatus(view: Record<string, unknown>, handle: string): string | undefined {
  const participants = view.participants;
  if (!Array.isArray(participants)) {
    return undefined;
  }
  for (const row of participants) {
    if (isRecord(row) && row.handle === handle && typeof row.status === "string") {
      return row.status;
    }
  }
  return undefined;
}

function ownerAgentChats(app: Asp, agent: string) {
  const owner = app.operatorHandle;
  const chats = [];
  for (const chat of app.store.chats.values()) {
    const people = app.store
      .participantsIn(chat.id)
      .filter((participant) => participant.status === "invited" || participant.status === "joined")
      .map((participant) => participant.handle);
    if (people.length === 2 && people.includes(owner) && people.includes(agent)) {
      chats.push(chat);
    }
  }
  return chats.sort((left, right) => left.created_at - right.created_at || left.id.localeCompare(right.id));
}

async function waitForOwnerAgentChat(app: Asp, agent: string) {
  await until(`owner 1:1 with ${agent}`, () => ownerAgentChats(app, agent).length > 0);
  const chat = ownerAgentChats(app, agent)[0];
  if (chat === undefined) {
    throw new Error(`no owner 1:1 with ${agent}`);
  }
  return chat;
}

async function until(label: string, ready: () => boolean, timeoutMs = 8_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (ready()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error(label);
}
