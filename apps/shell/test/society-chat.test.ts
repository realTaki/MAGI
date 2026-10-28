import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { MAGI_CONTACT_ID, messageDelivery } from "@magi/bus";
import { Magi } from "@magi/launcher/composition/agent.js";
import { createOwnerApi } from "@magi/launcher/composition/owner.js";

async function until(read: () => boolean, timeout = 8_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (read()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
  throw new Error("direct society chat did not arrive");
}
test("operator DMs independent EVAs and their direct peer replies do not create echo loops", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "magi-direct-society-"));
  const calls = [0, 0, 0];
  const nodes = ["one", "two", "three"].map((name, index) => new Magi(`@${name}.magi`, { workspace: path.join(root, name), peer: { port: 0 }, owner: true,
    client: { async complete() { calls[index]++; return { role: "assistant" as const, content: `reply:${name}` }; } } }));
  try {
    for (const node of nodes) await node.start();
    for (const node of nodes) for (const other of nodes) if (node !== other) node.bus.contacts.savePeer(node.bus.contacts.forPeer({ principal: other.bus.settings.get("peer.principal")!, endpoint: other.bus.settings.get("peer.reachableURL")! }).id);
    for (const node of nodes) { const owner = createOwnerApi(node.bus); owner["owner.chat"]({ content: "owner hello" }); await until(() => owner["owner.events"]().some(message => message.role === "assistant" && message.content === `reply:${node.bus.handle.slice(1).split(".")[0]}`)); }
    assert.deepEqual(calls, [1, 1, 1]);
    const target = nodes[0].bus.chats.forPeer(nodes[1].bus.settings.get("peer.principal")!);
    const job = messageDelivery.send(nodes[0].bus, target.id, "peer hello", MAGI_CONTACT_ID);
    await until(() => nodes[0].bus.messages.list(target.id).some(message => message.content === "reply:two"));
    assert.equal(nodes[0].bus.board("MessageDeliveryJob").result(job)?.status, "completed");
    assert.deepEqual(calls, [1, 2, 1]);
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.deepEqual(calls, [1, 2, 1]);
  } finally { for (const node of nodes) await node.stop(); await rm(root, { recursive: true, force: true }); }
});
