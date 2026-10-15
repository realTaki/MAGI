import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { LocalDatabase } from "@magi/asp-server/db/database.js";
import { isRecord, request, tempRoot, withApp } from "./helpers.js";

/*
 * Business flow: switching the model, ASP side (`ARCHITECTURE.md`, "ASP").
 * `PUT /settings/provider` forwards one complete update to the MAGI that are
 * connected, reports which synced, and keeps no copy of the key.
 */

test("provider update is transient", async (t) => {
  const databasePath = path.join(tempRoot(t), "asp.sqlite");
  await withApp({ databasePath }, async (app) => {
    const denied = await request(app, "PUT", "/settings/provider", { body: { api_key: "secret" } });
    assert.equal(denied.status, 401);
    const token = operatorToken(await request(app, "GET", "/operator"));
    const saved = await request(app, "PUT", "/settings/provider", {
      token,
      body: { provider: "claude", model: "claude-opus-5", api_key: "sk-test" },
    });
    assert.deepEqual(saved.data, {
      provider: "claude",
      model: "claude-opus-5",
      api_key: "sk-test",
      base_url: null,
      synced: [],
      failed: [],
    });
  });
  const database = new LocalDatabase(databasePath);
  database.open();
  try {
    assert.equal(database.getSetting("provider"), null);
  } finally {
    database.close();
  }
});

test("rejected provider is reported to the app", async (t) => {
  await withApp(
    { databasePath: path.join(tempRoot(t), "asp.sqlite"), aspSeed: { "@eva-000.magi": "magi-token" } },
    async (app) => {
      app.transport.updateProvider = async () => false;
      const token = operatorToken(await request(app, "GET", "/operator"));
      const result = record(
        (
          await request(app, "PUT", "/settings/provider", {
            token,
            body: { provider: "openai", model: "gpt-5.6", api_key: "bad-key" },
          })
        ).data,
      );
      assert.deepEqual(result.synced, []);
      assert.deepEqual(result.failed, [
        { handle: "@eva-000.magi", detail: "MAGI rejected provider configuration" },
      ]);
    },
  );
});

function operatorToken(response: { data: unknown }): string {
  if (!isRecord(response.data) || typeof response.data.token !== "string") {
    throw new Error("operator token missing");
  }
  return response.data.token;
}

function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error("expected an object");
  }
  return value;
}
