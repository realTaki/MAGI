import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { isRecord, request, tempRoot, withApp } from "./helpers.ts";
import { RecordingSpawner } from "../server/spawn.ts";

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

test("only the operator can stop and restart managed MAGI", async (t) => {
  const spawner = new RecordingSpawner();
  await withApp({ databasePath: path.join(tempRoot(t), "asp.sqlite"), magiSpawner: spawner }, async (app) => {
    const token = app.operatorToken;
    await request(app, "POST", "/conversations", { token, body: { kind: "bot" } });
    assert.equal(spawner.calls.length, 1);
    assert.equal((await request(app, "POST", "/runtime/magi/stop")).status, 401);
    assert.equal((await request(app, "POST", "/runtime/magi/stop", { token })).status, 200);
    const started = await request(app, "POST", "/runtime/magi/start", { token });
    assert.equal(started.status, 200);
    assert.deepEqual(started.data, { started: 1 });
    assert.equal(spawner.calls.length, 2);
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
