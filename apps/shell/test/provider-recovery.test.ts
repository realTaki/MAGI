import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { Bus, encryptFor, messageDelivery, MAGI_CONTACT_ID, type CardSpec } from "@magi/bus";
import { ProvidersWorker } from "@magi/providers/worker.js";
import { PiAiClient } from "@magi/providers/client.js";
import { providerFailureCode } from "@magi/bus/jobs/providerFailure.js";
import { Magi } from "@magi/launcher/composition/agent.js";

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
