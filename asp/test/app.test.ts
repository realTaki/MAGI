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
    assert.equal(response.data.handle, "user");
    assert.equal(typeof response.data.token, "string");
    assert.notEqual(response.data.token, "");
    operator = response.data;
  });
  await withApp({ databasePath }, async (app) => {
    const response = await request(app, "GET", "/operator");
    assert.deepEqual(response.data, operator);
  });
});

test("ASP never starts a MAGI process of its own", async (t) => {
  await withApp({ databasePath: path.join(tempRoot(t), "asp.sqlite") }, async (app) => {
    const token = app.operatorToken;
    const created = await request(app, "POST", "/conversations", { token, body: { kind: "bot" } });
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
      ["user"],
    );
    assert.equal((agents as Array<Record<string, unknown>>)[0]?.managed, false);
  });
});

test("a previous desktop session can request ASP shutdown as operator", async (t) => {
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
