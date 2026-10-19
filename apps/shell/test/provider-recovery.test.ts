import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { Bus, encryptFor, messageDelivery, MAGI_CONTACT_ID, type CardSpec } from "@magi/bus";
import { ProvidersWorker } from "@magi/providers/worker.js";
import { AspWorker } from "@magi/channel-asp/worker.js";
import { PiAiClient } from "@magi/providers/client.js";
import { providerFailureCode } from "@magi/bus/jobs/providerFailure.js";
import { Asp } from "../../asp/asp.ts";
import { Magi } from "../../eva/eva.ts";

async function until(read: () => unknown, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (read()) return; await sleep(10); }
  throw new Error("mock acceptance timed out");
}
function sse() {
  const data = [
    ["message_start", { type: "message_start", message: { id: "mock", type: "message", role: "assistant", model: "MiniMax-M3", content: [], stop_reason: null, usage: { input_tokens: 1, output_tokens: 0 } } }],
    ["message_delta", { type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { output_tokens: 1 } }],
    ["message_stop", { type: "message_stop" }],
  ];
  return new Response(data.map(([event, value]) => `event: ${event}\ndata: ${JSON.stringify(value)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });
}
test("a timed-out verifier cannot save a late result", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-probe-timeout-"));
  const bus = new Bus("@timeout.magi", path);
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  let configured = false;
  const worker = new ProvidersWorker(bus, { async complete() { return { role: "assistant", content: "" }; }, verify() { return pending; }, configure() { configured = true; } }, 20);
  try {
    const id = bus.board("ChangeProviderNotify").publish({ provider: "minimax-cn", model: "MiniMax-M3", api_key: "synthetic-timeout" }, "test");
    await worker.poll();
    assert.equal(providerFailureCode(bus.board("ChangeProviderNotify").result(id)?.error), "timeout");
    finish(); await sleep(30);
    assert.equal(configured, false);
    assert.equal(bus.board("ConfigureJob").claim("test"), null);
    assert.equal(bus.secrets.get("provider.api_key"), null);
  } finally { bus.close(); await rm(path, { recursive: true, force: true }); }
});
test("a socket failure requeues a provider receipt instead of losing its saved state", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-receipt-delivery-"));
  const bus = new Bus("@receipt.magi", path);
  const worker = new AspWorker(bus);
  let calls = 0;
  Object.assign(worker, { client: { connected: true, async send() { if (++calls === 1) throw new Error("socket disconnected"); } }, keyReady: true });
  try {
    const chat = bus.chats.forChannel("asp", "mock-room");
    const id = messageDelivery.send(bus, chat.id, "card: saved", MAGI_CONTACT_ID, "test", { card_status: { id: "card", state: "saved", attempt: "one" } });
    await worker.poll();
    assert.equal(bus.board("MessageDeliveryJob").result(id), null);
    Object.assign(worker, { nextCardDelivery: 0 });
    await worker.poll();
    assert.equal(bus.board("MessageDeliveryJob").result(id)?.status, "completed");
    assert.equal(calls, 2);
  } finally { bus.close(); await rm(path, { recursive: true, force: true }); }
});
test("fresh MiniMax configuration distinguishes Hi-before-save and verification failures, retries directly, and survives an ASP restart", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-provider-asp-"));
  const silent = { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } };
  const options = { databasePath: join(path, "asp.sqlite"), workspace: join(path, "casper"), host: "127.0.0.1", port: 0, client: silent };
  let asp = new Asp(options);
  let magi: Magi | undefined;
  let calls = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  try {
    await asp.start();
    const origin = asp.origin;
    const port = Number(new URL(origin).port);
    const request = async (route: string, body?: unknown) => {
      const response = await fetch(new URL(route, origin), { method: body ? "POST" : "GET", headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
      assert.ok(response.ok, `mock ASP ${route} ${response.status}`);
      return response.json() as Promise<any>;
    };
    const created = await request("/chats", { kind: "bot" });
    const fakeFetch = (async () => {
      calls++;
      if (calls === 1) throw new TypeError("fetch failed");
      if (calls === 2) return Response.json({ error: { type: "authentication_error" } }, { status: 401 });
      if (calls === 3) return Response.json({ error: { message: "model not supported" } }, { status: 400 });
      await gate; return sse();
    }) as typeof fetch;
    magi = new Magi(created.magi.handle, { workspace: join(path, "receiver"), asp: { base: origin, token: created.magi.token }, client: new PiAiClient({}, fakeFetch) });
    await magi.start();
    await until(() => magi!.bus.settings.get("provider.card.spec"));
    const card = JSON.parse(magi.bus.settings.get("provider.card.spec")!) as CardSpec;
    const chat = magi.bus.chats.list("asp")[0];
    await request(`/chats/${chat.delivery_address}/join`, {});
    await request(`/chats/${chat.delivery_address}/messages`, { content: "Hi" });
    await until(() => magi!.bus.messages.list(chat.id).some((row) => row.content.includes("ProviderError [unconfigured]")));
    assert.equal(calls, 0, "Hi before configuration does not contact the provider");
    const fields = { provider: "minimax-cn", model: "MiniMax-M3", base_url: "", "provider.api_key": { ciphertext: encryptFor(magi.bus.identity.publicKey, "synthetic-integration") } };
    for (const [index, error] of ["connection", "authentication", "model"].entries()) {
      const attempt = `try-${index}`;
      await request(`/chats/${chat.delivery_address}/messages`, { content: "submitted", metadata: { card_result: { id: card.id, attempt, fields } } });
      await until(() => (() => { const receipt = JSON.parse(magi!.bus.settings.get(`card.receipt.${card.id}`) ?? "{}"); return receipt.attempt === attempt && receipt.state === "failed"; })());
      assert.equal(JSON.parse(magi.bus.settings.get(`card.receipt.${card.id}`)!).error, error);
      assert.equal(magi.bus.secrets.get("provider.api_key"), null);
      await request(`/chats/${chat.delivery_address}/messages`, { content: "status", metadata: { card_result: { id: card.id, action: "status", attempt } } });
      await sleep(30);
    }
    const submission = { content: "submitted", metadata: { card_result: { id: card.id, attempt: "success", fields } } };
    await request(`/chats/${chat.delivery_address}/messages`, submission);
    await until(() => calls === 4);
    await asp.stop();
    release();
    await until(() => JSON.parse(magi!.bus.settings.get(`card.receipt.${card.id}`) ?? "{}").state === "saved");
    assert.equal(magi.bus.secrets.get("provider.api_key"), "synthetic-integration");
    asp = new Asp({ ...options, port });
    await asp.start();
    await request(`/chats/${chat.delivery_address}/messages`, submission);
    let saved = false;
    for (let i = 0; i < 300; i++) {
      const events = await request(`/chats/${chat.delivery_address}/events`);
      saved = events.events.some((event: any) => event.payload?.sender === magi!.bus.handle && event.payload?.metadata?.card_status?.state === "saved");
      if (saved) break;
      await sleep(20);
    }
    assert.ok(saved, "saved acknowledgement is delivered after reconnection");
    assert.equal(calls, 4, "replayed submission cannot call a paid model twice");
  } finally { release(); await magi?.stop(); await asp.stop(); await rm(path, { recursive: true, force: true }); }
});
