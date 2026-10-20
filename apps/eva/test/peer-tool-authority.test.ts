/** Private-LAN deployment intentionally has no per-peer tool authorization layer. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { Bus, messageDelivery } from "@magi/bus";
import { ToolsWorker } from "@magi/built-in-tools/worker.js";

test("trusted-LAN peer uses ordinary tools without contact approval or capability policy", async () => {
  const root = mkdtempSync(join(tmpdir(), "magi-peer-tools-")); const bus = new Bus("@test.magi", root);
  let invoked = 0;
  const worker = new ToolsWorker(bus, [{ name: "bash", description: "Harmless test stub; no process runs", input_schema: {}, async run() { invoked++; return "stub result"; } }]);
  try {
    const receipt = messageDelivery.receivePeer(bus, { principal: "d".repeat(64), endpoint: "http://192.168.1.8:8080", conversationUUID: randomUUID(), messageId: randomUUID(), content: "Use the available tool" });
    const id = bus.board("RunToolJob").publish({ call: { name: "bash", tool_call_id: "trusted-peer", arguments: {} }, chat_id: receipt.chat_id, message_id: receipt.message_id }, "agent");
    await worker.poll(); await worker.stop();
    assert.equal(bus.board("RunToolJob").result(id)?.status, "completed");
    assert.equal(invoked, 1);
  } finally { await worker.stop(); bus.close(); rmSync(root, { recursive: true, force: true }); }
});

test("ordinary tool jobs retain sender context without access-control filtering", () => {
  const root = mkdtempSync(join(tmpdir(), "magi-peer-context-")); const bus = new Bus("@test.magi", root);
  try {
    const receipt = messageDelivery.receivePeer(bus, { principal: "e".repeat(64), endpoint: "http://eva.internal", conversationUUID: randomUUID(), messageId: randomUUID(), content: "configure" });
    const id = bus.board("RunToolJob").publish({ call: { name: "provider_settings", tool_call_id: "ordinary", arguments: { action: "show" } }, chat_id: receipt.chat_id, message_id: receipt.message_id }, "agent");
    const job = bus.board("RunToolJob").claimId("providers", id);
    assert.equal(job?.input.message_id, receipt.message_id);
    assert.equal(job?.input.chat_id, receipt.chat_id);
  } finally { bus.close(); rmSync(root, { recursive: true, force: true }); }
});
