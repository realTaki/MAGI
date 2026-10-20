import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Bus } from "../bus.js";
import { groupPrincipal, type GroupEventEnvelope } from "../books/groupBook.js";

function cluster() {
  const roots = [0, 1, 2].map(() => mkdtempSync(join(tmpdir(), "magi-group-")));
  const buses = roots.map((root, index) => new Bus(`@g${index}.magi`, root));
  const peers = buses.map((bus, index) => ({ principal: groupPrincipal(bus.identity.publicKey), endpoint: `http://127.0.0.1:${40000 + index}` }));
  for (const [index, bus] of buses.entries()) {
    bus.settings.set("peer.reachableURL", peers[index].endpoint);
    for (const peer of peers) if (peer.principal !== peers[index].principal) bus.contacts.savePeer(bus.contacts.forPeer(peer).id);
  }
  return { roots, buses, peers, clean: () => { buses.forEach(bus => bus.close()); roots.forEach(root => rmSync(root, { recursive: true, force: true })); } };
}
function replicate(source: Bus, target: Bus, uuid: string, limit = 2) {
  let after = 0;
  do {
    const page = source.groups.events(uuid, { after, limit });
    page.events.forEach(event => target.groups.ingest(event));
    if (page.nextCursor === null) break;
    after = page.nextCursor;
  } while (true);
}

test("three peers converge after offline, reorder, duplicate and gapped sequence delivery", () => {
  const f = cluster(); const [a, b, c] = f.buses;
  try {
    const group = a.groups.create({ title: "Explicit group", members: f.peers.slice(1) }); const uuid = group.conversation_uuid;
    for (const peer of [b, c]) { assert.equal(peer.groups.offerMembership(a.groups.membership(uuid)).conversation_uuid, uuid); }
    const a1 = a.groups.append(uuid, "A one").event, a2 = a.groups.append(uuid, "A two").event;
    b.groups.ingest(a2); b.groups.ingest(a2); // seq 2 arrives before seq 1; a high-water cursor would lose it.
    b.groups.append(uuid, "B concurrent"); c.groups.append(uuid, "C offline");
    c.close(); f.buses[2] = new Bus("@g2.magi", f.roots[2]); const restarted = f.buses[2];
    b.groups.ingest(a1);
    replicate(a, restarted, uuid); replicate(b, restarted, uuid); replicate(restarted, a, uuid); replicate(restarted, b, uuid);
    const history = a.groups.history(uuid).map(event => event.payload);
    assert.equal(history.length, 4);
    assert.deepEqual(b.groups.history(uuid).map(event => event.payload), history);
    assert.deepEqual(restarted.groups.history(uuid).map(event => event.payload), history);
    assert.equal(restarted.groups.ingest(a1).duplicate, true);
    assert.throws(() => restarted.groups.events(uuid, { limit: 101 }), /page/);
  } finally { f.clean(); }
});

test("membership epochs preserve routing order and removed participants leave the sync set", () => {
  const f = cluster(); const [a, b, c] = f.buses;
  try {
    const group = a.groups.create({ title: "Members", members: f.peers.slice(1) }); const uuid = group.conversation_uuid;
    const invitation = a.groups.membership(uuid); b.groups.offerMembership(invitation);
    const event = a.groups.append(uuid, "private group message").event;
    assert.equal(b.groups.ingest(event).duplicate, false); c.groups.offerMembership(invitation);
    assert.throws(() => b.groups.updateMembers(uuid, f.peers.slice(0, 2)), /creator/);
    const forged = structuredClone(invitation); forged.payload.members = f.peers.slice(0, 2); forged.payload.epoch = 2;
    assert.throws(() => b.groups.offerMembership(forged), /gap or conflict/);
    const epoch2 = a.groups.updateMembers(uuid, f.peers.slice(0, 2)); b.groups.offerMembership(epoch2); c.groups.offerMembership(epoch2);
    assert.equal(a.groups.allows(uuid, f.peers[2].principal), false);
    assert.equal(c.groups.allows(uuid, f.peers[2].principal), false);
    assert.throws(() => c.groups.append(uuid, "removed"), /current group member/);
    assert.equal(b.groups.ingest(event).duplicate, true); // Historical epochs remain readable.
  } finally { f.clean(); }
});

test("forwarded original author is retained and conflicting duplicate content is rejected", () => {
  const f = cluster(); const [a, b] = f.buses;
  try {
    const group = a.groups.create({ title: "Signed", members: f.peers.slice(1) }); const uuid = group.conversation_uuid;
    b.groups.offerMembership(a.groups.membership(uuid));
    const request = a.groups.append(uuid, "request"); b.groups.ingest(request.event);
    const answer = b.groups.append(uuid, "reply", request.event.payload.eventId);
    const receipt = a.groups.ingest(answer.event); assert.equal(receipt.job_id, null);
    const forged = structuredClone(answer.event) as GroupEventEnvelope; forged.payload.content = "forged";
    assert.throws(() => a.groups.ingest(forged), /reused/);
    assert.equal(a.groups.history(uuid).at(-1)?.payload.author, f.peers[1].principal);
  } finally { f.clean(); }
});


test("historical event does not revert an explicitly changed peer endpoint", () => {
  const f = cluster(); const [a, b] = f.buses;
  try {
    const group = a.groups.create({ title: "Moved peer", members: f.peers.slice(1) }); const uuid = group.conversation_uuid;
    b.groups.offerMembership(a.groups.membership(uuid));
    const beforeMove = a.groups.append(uuid, "before move").event;
    const contact = b.contacts.list().find(contact => contact.peer_id === f.peers[0].principal)!;
    b.contacts.savePeer(contact.id, "https://new.example");
    b.groups.ingest(beforeMove);
    assert.equal(b.contacts.get(contact.id)?.peer_url, "https://new.example");
  } finally { f.clean(); }
});
