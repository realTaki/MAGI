import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { isRecord, request, tempRoot, withApp } from "./helpers.ts";

test("health creates the versioned local database", async (t) => {
  const databasePath = path.join(tempRoot(t), "asp.sqlite");
  await withApp({ databasePath }, async (app) => {
    const health = await request(app, "GET", "/health");
    assert.deepEqual(health.data, { status: "ok", runtime: "typescript" });
    assert.equal(app.database.path, databasePath);
  });
  const { existsSync } = await import("node:fs");
  assert.equal(existsSync(databasePath), true);
});

test("operator bootstrap is stable across restarts", async (t) => {
  const databasePath = path.join(tempRoot(t), "asp.sqlite");
  let operator: unknown;
  await withApp({ databasePath }, async (app) => {
    const response = await request(app, "GET", "/operator");
    assert.equal(response.status, 200);
    assert.ok(isRecord(response.data));
    assert.equal(response.data.handle, "@user");
    assert.equal(typeof response.data.token, "string");
    assert.notEqual(response.data.token, "");
    operator = response.data;
  });
  await withApp({ databasePath }, async (app) => {
    const response = await request(app, "GET", "/operator");
    assert.deepEqual(response.data, operator);
  });
});

test("a previous bare user identity migrates to @user without losing its chats", async (t) => {
  const databasePath = path.join(tempRoot(t), "asp.sqlite");
  let token = "";
  let chatId = "";
  await withApp({ databasePath }, async (app) => {
    token = app.operatorToken;
    const created = await request(app, "POST", "/chats", { token, body: { kind: "group" } });
    chatId = String((created.data as { chat_id?: unknown }).chat_id);
    await request(app, "POST", `/chats/${chatId}/messages`, { token, body: { content: "hello" } });
    const connection = app.database.connection!;
    const agent = connection.prepare("SELECT record_json FROM asp_agents WHERE handle = '@user'").get() as { record_json: string };
    connection.prepare("UPDATE asp_agents SET handle = 'user', record_json = ? WHERE handle = '@user'")
      .run(JSON.stringify({ ...JSON.parse(agent.record_json) as Record<string, unknown>, handle: "user" }));
    connection.prepare("UPDATE asp_participants SET handle = 'user' WHERE handle = '@user'").run();
    connection.prepare("UPDATE asp_events SET payload_json = replace(payload_json, '\"@user\"', '\"user\"')").run();
    connection.prepare("UPDATE asp_settings SET value_json = ? WHERE key = 'operator'")
      .run(JSON.stringify({ handle: "user", token }));
    // No migration bookkeeping to unpick: the relay repairs a database that still
    // names the operator `user` whenever it opens one.
  });
  await withApp({ databasePath }, async (app) => {
    assert.equal(app.operatorHandle, "@user");
    assert.equal(app.operatorToken, token);
    const view = await request(app, "GET", `/chats/${chatId}`, { token });
    const participants = isRecord(view.data) && Array.isArray(view.data.participants) ? view.data.participants : [];
    assert.equal(participants.some((row) => isRecord(row) && row.handle === "@user"), true);
    const events = await request(app, "GET", `/chats/${chatId}/events`, { token });
    const messages = isRecord(events.data) && Array.isArray(events.data.events) ? events.data.events : [];
    assert.equal(messages.some((row) => isRecord(row) && isRecord(row.payload) && row.payload.sender === "@user"), true);
  });
});

test("ASP never starts a MAGI process of its own", async (t) => {
  await withApp({ databasePath: path.join(tempRoot(t), "asp.sqlite") }, async (app) => {
    const token = app.operatorToken;
    const created = await request(app, "POST", "/chats", { token, body: { kind: "bot" } });
    assert.equal(created.status, 201);
    // The desktop app runs MAGI; ASP only knows who they are.
    assert.equal((await request(app, "POST", "/runtime/magi/start", { token })).status, 404);
    assert.equal((await request(app, "POST", "/runtime/magi/stop", { token })).status, 404);
    const agents = isRecord(created.data) && isRecord(created.data.magi) ? created.data.magi : {};
    assert.equal(typeof agents.token, "string");
  });
});

test("the agent roster is operator only and carries the runner credentials", async (t) => {
  await withApp({ databasePath: path.join(tempRoot(t), "asp.sqlite") }, async (app) => {
    const token = app.operatorToken;
    assert.equal((await request(app, "GET", "/agents")).status, 401);
    const listed = await request(app, "GET", "/agents", { token });
    assert.equal(listed.status, 200);
    const agents = isRecord(listed.data) ? listed.data.agents : null;
    // The operator itself is not a managed MAGI.
    assert.deepEqual(
      (agents as Array<Record<string, unknown>>).map((agent) => agent.handle),
      ["@user"],
    );
    assert.equal((agents as Array<Record<string, unknown>>)[0]?.managed, false);
  });
});

test("a previous desktop chat can request ASP shutdown as operator", async (t) => {
  let requested = false;
  await withApp({
    databasePath: path.join(tempRoot(t), "asp.sqlite"),
    requestShutdown: () => { requested = true; },
  }, async (app) => {
    assert.equal((await request(app, "POST", "/runtime/asp/stop")).status, 401);
    assert.equal((await request(app, "POST", "/runtime/asp/stop", { token: app.operatorToken })).status, 200);
    await new Promise((resolve) => setTimeout(resolve, 60));
    assert.equal(requested, true);
  });
});
