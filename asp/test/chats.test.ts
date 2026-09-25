import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { connect, isRecord, receiveInvite, request, tempRoot, withApp } from "./helpers.ts";

/*
 * Business flow: creating a chat (`ARCHITECTURE.md`, "ASP").
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
    assert.equal(body.name, "eva-000");
    assert.deepEqual(body.agents, ["@eva-000.magi"]);
    const magi = record(body.magi);
    assert.equal(magi.name, "eva-000");
    assert.equal(magi.handle, "@eva-000.magi");
    assert.equal(typeof magi.token, "string");
    // ASP registers the agent; starting it belongs to the desktop app.
    assert.equal("spawned" in body, false);
    const roster = recordList(record((await request(app, "GET", "/agents", { token })).data).agents);
    assert.deepEqual(roster.filter((agent) => agent.managed === true), [
      {
        handle: "@eva-000.magi",
        token: magi.token,
        name: "eva-000",
        nickname: null,
        managed: true,
        online: false,
      },
    ]);
    const listed = record(await request(app, "GET", "/chats", { token }).then((response) => response.data));
    const chats = listed.chats;
    assert.ok(Array.isArray(chats));
    assert.equal(record(chats[0]).chat_id, body.chat_id);
    assert.deepEqual(record(chats[0]).agents, body.agents);
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
        (agent) => agent.managed === true,
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
    assert.equal(body.name, "eva-000");
    assert.deepEqual(body.agents, ["@eva-000.magi"]);
  });
});

test("bots lists spawned magi not a static roster", async (t) => {
  await withApp({ databasePath: databasePath(tempRoot(t)) }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const empty = await request(app, "GET", "/bots", { token });
    assert.equal(empty.status, 200);
    assert.deepEqual(empty.data, { bots: [] });
    const first = await request(app, "POST", "/chats", { token, body: { kind: "bot" } });
    const second = await request(app, "POST", "/chats", { token, body: { kind: "bot" } });
    assert.equal(first.status, 201);
    assert.equal(second.status, 201);
    const firstBody = record(first.data);
    const secondBody = record(second.data);
    assert.equal(firstBody.name, "eva-000");
    assert.equal(secondBody.name, "eva-001");
    assert.deepEqual(firstBody.agents, ["@eva-000.magi"]);
    assert.deepEqual(secondBody.agents, ["@eva-001.magi"]);
    const listed = record((await request(app, "GET", "/bots", { token })).data).bots;
    assert.ok(Array.isArray(listed));
    assert.deepEqual(
      new Set(listed.map((row) => record(row).handle)),
      new Set(["@eva-000.magi", "@eva-001.magi"]),
    );
    assert.deepEqual(new Set(listed.map((row) => record(row).name)), new Set(["eva-000", "eva-001"]));
    assert.equal(listed.every((row) => record(row).online === false), true);
    assert.equal(listed.some((row) => record(row).handle === "@user"), false);
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
      { handle, name: "eva-000", online: false, in_chat: false },
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
      { handle, name: "eva-000", online: false, in_chat: true },
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
    assert.deepEqual(online, [{ handle, name: bot.name, online: true, in_chat: false }]);
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
