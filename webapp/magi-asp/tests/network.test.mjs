import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import WebSocket from "ws";

import { startWebapp } from "../../server.mjs";

async function request(network, path, { token, method = "GET", body } = {}) {
  const response = await fetch(`${network.url}${path}`, {
    method,
    headers: {
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(body ? { "content-type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, body: await response.json() };
}

test("the local operator persists trusted sessions and delivers WebSocket events", async () => {
  const dataDir = mkdtempSync(join(tmpdir(), "magi-asp-"));
  const distDir = mkdtempSync(join(tmpdir(), "magi-webapp-dist-"));
  writeFileSync(join(distDir, "index.html"), "<h1>MAGI</h1>");
  let network = await startWebapp({ dataDir, distDir, port: 0 });
  try {
    assert.equal(network.databasePath, join(dataDir, "app.sqlite"));
    assert.equal(existsSync(join(dataDir, "asp.sqlite")), false);
    assert.equal(await (await fetch(network.url)).text(), "<h1>MAGI</h1>");
    network.appStore.setSetting("operator", "local");
    assert.deepEqual((await request(network, "/asp/health")).body, { status: "ok", protocol: "asp/0.1" });
    const alice = (await request(network, "/asp/agents", { method: "POST", body: { handle: "@magi.alice" } })).body;
    const bob = (await request(network, "/asp/agents", { method: "POST", body: { handle: "@magi.bob", policy: "allowlist" } })).body;

    await request(network, `/asp/agents/${encodeURIComponent(bob.handle)}/allowlist/${encodeURIComponent(alice.handle)}`, {
      method: "PUT", token: bob.token,
    });
    const created = await request(network, "/asp/sessions", {
      method: "POST",
      token: alice.token,
      body: { invite: [bob.handle], topic: "hello" },
    });
    assert.equal(created.status, 201);
    const sessionId = created.body.session_id;
    assert.equal((await request(network, `/asp/sessions/${sessionId}/join`, { method: "POST", token: bob.token })).status, 200);

    const socket = new WebSocket(`${network.url.replace("http", "ws")}/asp/connect`, { headers: { authorization: `Bearer ${bob.token}` } });
    const delivered = new Promise((resolve, reject) => {
      socket.once("error", reject);
      socket.on("message", (raw) => {
        const event = JSON.parse(raw.toString());
        if (event.type === "session.message" && event.payload.content[0].text === "ping") resolve(event);
      });
    });
    await new Promise((resolve, reject) => {
      socket.once("open", resolve);
      socket.once("error", reject);
    });

    const sent = await request(network, `/asp/sessions/${sessionId}/messages`, {
      method: "POST",
      token: alice.token,
      body: { content: [{ type: "text", text: "ping" }] },
    });
    assert.equal(sent.status, 201);
    assert.equal((await delivered).payload.sender, alice.handle);
    socket.close();

    const events = await request(network, `/asp/sessions/${sessionId}/events`, { token: bob.token });
    assert.deepEqual(events.body.events.map((event) => event.type), ["session.invited", "session.joined", "session.message"]);
    assert.equal(events.body.events.at(-1).sequence, sent.body.sequence);

    await network.close();
    network = await startWebapp({ dataDir, distDir, port: 0 });
    assert.equal(network.appStore.getSetting("operator"), "local");
    const replayed = await request(network, `/asp/sessions/${sessionId}/events`, { token: bob.token });
    assert.equal(replayed.body.events.at(-1).payload.content[0].text, "ping");
  } finally {
    await network.close();
    rmSync(dataDir, { recursive: true, force: true });
    rmSync(distDir, { recursive: true, force: true });
  }
});
