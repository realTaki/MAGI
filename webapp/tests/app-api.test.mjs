import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { startWebapp } from "../server.mjs";

async function request(network, path, { method = "GET", body } = {}) {
  const response = await fetch(`${network.url}${path}`, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: response.status, body: await response.json() };
}

test("the Webapp API owns settings and local conversation metadata", async () => {
  const dataDir = mkdtempSync(join(tmpdir(), "magi-app-api-"));
  const distDir = mkdtempSync(join(tmpdir(), "magi-webapp-dist-"));
  writeFileSync(join(distDir, "index.html"), "<h1>MAGI</h1>");
  const network = await startWebapp({ dataDir, distDir, port: 0 });
  try {
    assert.deepEqual(await request(network, "/api/health"), { status: 200, body: { status: "ok" } });
    assert.deepEqual(await request(network, "/api/settings/locale", { method: "PUT", body: { value: "zh" } }), { status: 200, body: { key: "locale", value: "zh" } });
    assert.deepEqual((await request(network, "/api/settings")).body, { settings: { locale: "zh" } });

    assert.equal((await request(network, "/api/conversations/conversation-1", {
      method: "PUT",
      body: { magiId: "magi.alice", title: "Hello" },
    })).status, 200);
    assert.equal((await request(network, "/api/magis/magi.alice/conversations")).body.conversations[0].title, "Hello");
  } finally {
    await network.close();
    rmSync(dataDir, { recursive: true, force: true });
    rmSync(distDir, { recursive: true, force: true });
  }
});
