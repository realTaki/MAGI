import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Bus } from "@magi/bus";
import { PeerWorker, principalFor } from "../worker.js";
import { PeerClient } from "../client.js";

async function cluster() {
  const root = mkdtempSync(join(tmpdir(), "magi-network-group-"));
  const buses = [0, 1, 2].map(index => new Bus(`@g${index}.magi`, join(root, String(index))));
  const workers = buses.map(bus => new PeerWorker(bus));
  for (const worker of workers) await worker.start();
  const peers = buses.map((bus, index) => ({ principal: principalFor(bus.identity.publicKey), endpoint: workers[index].origin! }));
  for (const bus of buses) for (const peer of peers) if (peer.principal !== principalFor(bus.identity.publicKey)) bus.contacts.acceptPeer(bus.contacts.forPeer(peer).id);
  return { root, buses, workers, peers, async close() { for (const worker of workers) await worker.stop(); for (const bus of buses) bus.close(); rmSync(root, { recursive: true, force: true }); } };
}
async function sync(worker: PeerWorker, uuid: string, principal: string) {
  let count = 0;
  while (await worker.groups.syncPeer(uuid, principal)) assert.ok(++count < 100, "bounded pages must progress");
}

test("three independent HTTP peers converge through offline restart and paginated gap recovery", async () => {
  const f = await cluster();
  try {
    const [a, b, c] = f.buses;
    const uuid = a.groups.create({ title: "Network group", members: f.peers.slice(1) }).conversation_uuid;
    for (const index of [1, 2]) {
      await sync(f.workers[0], uuid, f.peers[index].principal);
      assert.equal(f.buses[index].groups.get(uuid)?.trust, "accepted");
    }
    const a1 = a.groups.append(uuid, "first").event;
    const a2 = a.groups.append(uuid, "second arrives first").event;
    await new PeerClient(a.identity).sendGroup(f.workers[1].origin!, f.peers[1].principal, { group: { event: a2 } });
    b.groups.append(uuid, "concurrent B");
    c.groups.append(uuid, "offline C");
    await f.workers[2].stop(); c.close();
    for (let i = 0; i < 19; i++) a.groups.append(uuid, `page item ${i}`);
    await assert.rejects(f.workers[0].groups.syncPeer(uuid, f.peers[2].principal));
    f.buses[2] = new Bus("@g2.magi", join(f.root, "2"));
    f.workers[2] = new PeerWorker(f.buses[2]); await f.workers[2].start();
    for (let round = 0; round < 2; round++) for (const i of [0, 1, 2]) for (const j of [0, 1, 2]) if (i !== j) await sync(f.workers[i], uuid, f.peers[j].principal);
    const history = a.groups.history(uuid).map(event => event.payload);
    assert.equal(history.length, 23);
    assert.deepEqual(b.groups.history(uuid).map(event => event.payload), history);
    assert.deepEqual(f.buses[2].groups.history(uuid).map(event => event.payload), history);
    assert.equal(history.filter(event => event.eventId === a1.payload.eventId).length, 1);
    assert.equal((await new PeerClient(a.identity).sendGroup(f.workers[1].origin!, f.peers[1].principal, { group: { event: a1 } })).duplicate, true);
  } finally { await f.close(); }
});

test("group sync chains membership pages, keeps current-member routing and detects conflicting event IDs", async () => {
  const f = await cluster();
  try {
    const [a, b] = f.buses;
    const uuid = a.groups.create({ title: "Epoch chain", members: f.peers.slice(1) }).conversation_uuid;
    await sync(f.workers[0], uuid, f.peers[1].principal);
    for (let i = 0; i < 18; i++) a.groups.updateMembers(uuid, f.peers);
    a.groups.append(uuid, "epoch 19");
    await sync(f.workers[0], uuid, f.peers[1].principal);
    await sync(f.workers[1], uuid, f.peers[0].principal);
    assert.equal(b.groups.get(uuid)?.epoch, 19);
    assert.equal(b.groups.history(uuid).length, 1);
    const tampered = structuredClone(a.groups.history(uuid)[0]); tampered.payload.content = "forged";
    await assert.rejects(new PeerClient(b.identity).sendGroup(f.workers[0].origin!, f.peers[0].principal, { group: { event: tampered } }));
    a.groups.updateMembers(uuid, f.peers.slice(0, 2));
    await assert.rejects(f.workers[0].groups.syncPeer(uuid, f.peers[2].principal), /not a current group member/);
    await assert.rejects(new PeerClient(f.buses[2].identity).sync(f.workers[0].origin!, f.peers[0].principal, { conversationUUID: uuid }), /403/);
  } finally { await f.close(); }
});


test("membership-only page acknowledgements recover a removed then re-added offline replica", async () => {
  const f = await cluster();
  try {
    const [a, b] = f.buses;
    const uuid = a.groups.create({ title: "Rejoin", members: f.peers.slice(1) }).conversation_uuid;
    await sync(f.workers[0], uuid, f.peers[1].principal);
    for (let epoch = 2; epoch <= 10; epoch++) a.groups.updateMembers(uuid, [f.peers[0], f.peers[2]]);
    a.groups.updateMembers(uuid, f.peers);
    a.groups.append(uuid, "after rejoin");
    await sync(f.workers[0], uuid, f.peers[1].principal);
    assert.equal(b.groups.get(uuid)?.epoch, 11);
    await sync(f.workers[1], uuid, f.peers[0].principal);
    assert.equal(b.groups.history(uuid)[0].payload.content, "after rejoin");
  } finally { await f.close(); }
});
