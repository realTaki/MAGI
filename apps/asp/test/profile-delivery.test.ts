import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { ASP_MAGI_HANDLE } from "@magi/bus";
import { connect, isRecord, request, tempRoot, withApp } from "./helpers.js";

test("nickname HTTP and websocket controls are removed", async (t) => {
  await withApp({ databasePath: path.join(tempRoot(t), "asp.sqlite") }, async (app) => {
    assert.equal((await request(app, "PATCH", `/bots/${encodeURIComponent(ASP_MAGI_HANDLE)}/nickname`, {
      token: app.operatorToken, body: { nickname: "Stolen" },
    })).status, 404);
    const { socket } = await connect(app, app.operatorToken);
    const frames: unknown[] = [];
    socket.on("message", (data) => frames.push(JSON.parse(String(data))));
    for (const type of ["agent.nickname.read", "agent.nickname.current", "agent.nickname.update", "agent.nickname.updated"]) {
      socket.send(JSON.stringify({ type, nickname: "Stolen", handle: ASP_MAGI_HANDLE, request_id: "old-control", ok: true }));
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(app.store.getAgent(ASP_MAGI_HANDLE)?.nickname, null);
    assert.equal(app.store.getAgent(app.operatorHandle)?.nickname, null);
    assert.equal(frames.some((frame) => isRecord(frame) && String(frame.type).startsWith("agent.nickname.")), false);
    socket.close();
  });
});

test("delivery projects only the authenticated member's profile and separates name from nickname", async (t) => {
  await withApp({ databasePath: path.join(tempRoot(t), "asp.sqlite") }, async (app) => {
    const created = await request(app, "POST", "/chats", { token: app.operatorToken, body: { kind: "bot" } });
    assert.ok(isRecord(created.data) && isRecord(created.data.magi));
    const { chat_id: chatId, magi } = created.data;
    assert.equal(typeof magi.token, "string");
    const handle = String(magi.handle);
    const token = String(magi.token);
    assert.equal((await request(app, "POST", `/chats/${String(chatId)}/join`, { token })).status, 200);
    const post = (credential: string, profile: unknown, extra = {}) => request(app, "POST", `/chats/${String(chatId)}/messages`, {
      token: credential, body: { content: "Profile delivery", metadata: { profile, ...extra } },
    });
    const empty = (credential: string, metadata: unknown) => request(app, "POST", `/chats/${String(chatId)}/messages`, {
      token: credential, body: { content: "", metadata },
    });
    assert.equal((await empty(token, { profile: { name: "ANNOUNCED", nickname: null } })).status, 201);
    assert.equal(app.store.getAgent(handle)?.name, "ANNOUNCED");
    assert.equal((await empty(token, {})).status, 422);
    assert.equal((await empty(token, { profile: { name: "BAD", nickname: 7 } })).status, 422);
    assert.equal((await empty(app.operatorToken, { profile: { name: "Operator", nickname: null } })).status, 422);
    assert.equal((await empty("invalid-token", { profile: { name: "BAD", nickname: null } })).status, 401);
    assert.equal(app.store.getAgent(handle)?.name, "ANNOUNCED");
    const originalCasper = { ...app.store.getAgent(ASP_MAGI_HANDLE)! };
    assert.equal((await post(token, { name: "NODE", nickname: "My chosen name", handle: ASP_MAGI_HANDLE }, { handle: ASP_MAGI_HANDLE })).status, 201);
    assert.equal(app.store.getAgent(handle)?.name, "NODE");
    assert.equal(app.store.getAgent(handle)?.nickname, "My chosen name");
    assert.deepEqual(app.store.getAgent(ASP_MAGI_HANDLE), originalCasper);
    assert.equal((await post(app.operatorToken, { name: "Operator", nickname: "Me", handle })).status, 201);
    assert.equal(app.store.getAgent(handle)?.name, "NODE");
    assert.equal(app.store.getAgent(handle)?.nickname, "My chosen name");
    assert.equal(app.store.getAgent(app.operatorHandle)?.name, "Operator");
    assert.equal((await post(token, { name: "RENAMED", nickname: null })).status, 201);
    assert.equal(app.store.getAgent(handle)?.name, "RENAMED");
    assert.equal(app.store.getAgent(handle)?.nickname, null);
    for (const profile of [null, [], { name: "", nickname: null }, { name: "bad\nname", nickname: null },
      { name: "x".repeat(81), nickname: null }, { name: "OK", nickname: 3 }, { name: "OK" }, { name: "OK", nickname: " " }]) {
      assert.equal((await post(token, profile)).status, 201);
      assert.equal(app.store.getAgent(handle)?.name, "RENAMED");
      assert.equal(app.store.getAgent(handle)?.nickname, null);
    }
    const other = await request(app, "POST", "/chats", { token: app.operatorToken, body: { kind: "bot" } });
    assert.ok(isRecord(other.data) && isRecord(other.data.magi));
    const outsider = app.store.getAgent(String(other.data.magi.handle))!;
    const before = { ...outsider };
    assert.equal((await post(outsider.token, { name: "UNAUTHORIZED", nickname: "Bad" })).status, 404);
    assert.equal((await empty(outsider.token, { profile: { name: "BAD", nickname: null } })).status, 404);
    assert.deepEqual(app.store.getAgent(outsider.handle), before);
    assert.equal((await post("invalid-token", { name: "UNAUTHORIZED", nickname: "Bad" })).status, 401);
  });
});

test("Casper registration preserves an existing user nickname on restart", async (t) => {
  const databasePath = path.join(tempRoot(t), "asp.sqlite");
  await withApp({ databasePath }, async (app) => {
    const casper = app.store.getAgent(ASP_MAGI_HANDLE)!;
    assert.equal(casper.name, "CASPER");
    assert.equal(casper.nickname, null);
    casper.nickname = "My Casper";
    app.store.updateAgent(casper);
  });
  await withApp({ databasePath }, async (app) => {
    assert.equal(app.store.getAgent(ASP_MAGI_HANDLE)?.nickname, "My Casper");
  });
});
