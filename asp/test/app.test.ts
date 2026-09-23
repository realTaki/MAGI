import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { isRecord, request, tempRoot, withApp } from "./helpers.ts";

test("health creates the versioned local database", async (t) => {
  const databasePath = path.join(tempRoot(t), "asp.sqlite");
  await withApp({ databasePath }, async (app) => {
    const health = await request(app, "GET", "/health");
    assert.deepEqual(health.data, { status: "ok" });
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
