import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { Bus } from "../bus.js";
import { messageDelivery } from "../jobs/messageDelivery.js";
import { SYSTEM_CONTACT_ID, MAGI_CONTACT_ID, validatePeerEndpoint } from "../books/contactBook.js";
import { openDatabase } from "../sqlite.js";

const principal = "a".repeat(64);
function fixture() {
  const root = mkdtempSync(join(tmpdir(), "magi-peer-"));
  const bus = new Bus("@peer-test.magi", root);
  return { bus, root, clean: () => { bus.close(); rmSync(root, { recursive: true, force: true }); } };
}
const incoming = () => ({ principal, endpoint: "http://127.0.0.1:43210", conversationUUID: randomUUID(), messageId: randomUUID(), content: "hello" });

test("trusted-LAN hello records a stable identity label and directly usable HTTP route", () => {
  const f = fixture();
  try {
    const peer = f.bus.contacts.forPeer({ principal, endpoint: "https://peer.example", name: "@user.magi" });
    assert.notEqual(peer.id, SYSTEM_CONTACT_ID);
    assert.equal(peer.role, "stranger"); assert.equal(peer.peer_trust, "accepted");
    assert.equal(f.bus.contacts.acceptPeer(peer.id).peer_trust, "accepted");
    const moved = f.bus.contacts.forPeer({ principal, endpoint: "http://192.168.1.8:8080" });
    assert.equal(moved.id, peer.id);
    assert.equal(moved.peer_url, "http://192.168.1.8:8080");
    assert.throws(() => validatePeerEndpoint("https://user:secret@example.com"), /credentials/);
    assert.equal(validatePeerEndpoint("http://eva.internal:8080"), "http://eva.internal:8080");
  } finally { f.clean(); }
});

test("lost ACK and restart retain sender-scoped dedup; conflicting reuse rejected", () => {
  const root = mkdtempSync(join(tmpdir(), "magi-peer-restart-"));
  let bus = new Bus("@test.magi", root);
  try {
    const input = incoming(); const first = messageDelivery.receivePeer(bus, input);
    const replay = messageDelivery.receivePeer(bus, input);
    assert.equal(replay.duplicate, true); assert.equal(replay.job_id, first.job_id);
    assert.equal(bus.messages.count(first.chat_id), 1);
    assert.throws(() => messageDelivery.receivePeer(bus, { ...input, content: "changed" }), /reused/);
    bus.close(); bus = new Bus("@test.magi", root);
    assert.equal(messageDelivery.receivePeer(bus, input).job_id, first.job_id);
    const other = messageDelivery.receivePeer(bus, { ...input, principal: "b".repeat(64) });
    assert.notEqual(other.message_id, first.message_id);
  } finally { bus.close(); rmSync(root, { recursive: true, force: true }); }
});

test("both crash windows reconcile books to exactly one job before ACK", () => {
  const root = mkdtempSync(join(tmpdir(), "magi-peer-crash-"));
  let bus = new Bus("@test.magi", root);
  try {
    const target = bus.chats.forPeer(principal);
    const contact = bus.contacts.forPeer({ principal, endpoint: "https://peer.example" });
    const orphan = bus.deliveries.persist({ chat_id: target.id, contact_id: contact.id, content: "crashed before publish", publisher: "peer" });
    const second = bus.deliveries.persist({ chat_id: target.id, contact_id: contact.id, content: "crashed after publish", publisher: "peer" });
    const id = bus.board("MessageDeliveryJob").publishOnce(second.delivery.delivery_key, { message_id: second.delivery.message_id }, "peer");
    bus.close(); bus = new Bus("@test.magi", root);
    assert.ok(bus.deliveries.get(orphan.delivery.message_id)?.job_id);
    assert.equal(bus.deliveries.get(second.delivery.message_id)?.job_id, id);
    bus.deliveries.reconcile(bus);
    const db = openDatabase(join(root, "logs", "jobs.db"));
    assert.equal(db.prepare("SELECT count(*) AS n FROM jobs WHERE type='MessageDeliveryJob'").get()?.n, 2);
    db.close();
  } finally { bus.close(); rmSync(root, { recursive: true, force: true }); }
});

test("replies reuse UUID and are history-only, preventing autonomous reply loops", () => {
  const f = fixture();
  try {
    const request = incoming(); const first = messageDelivery.receivePeer(f.bus, request);
    const metadata = messageDelivery.replyMetadata(f.bus, first.message_id);
    const job = messageDelivery.send(f.bus, first.chat_id, "answer", MAGI_CONTACT_ID, "agent", metadata);
    const output = f.bus.board("MessageDeliveryJob").peekInput(job)!;
    assert.equal((output.metadata?.peer as { replyTo: string }).replyTo, request.messageId);
    assert.equal((output.metadata?.peer as { conversationUUID: string }).conversationUUID, request.conversationUUID);
    const reply = messageDelivery.receivePeer(f.bus, { ...request, messageId: randomUUID(), content: "answer", replyTo: request.messageId });
    assert.equal(reply.job_id, null);
    assert.equal(messageDelivery.receivePeer(f.bus, { ...request, messageId: "reply", replyTo: request.messageId }).job_id, null);
  } finally { f.clean(); }
});

test("offline retries have durable bounded backoff across restart", () => {
  const root = mkdtempSync(join(tmpdir(), "magi-peer-retry-")); let bus = new Bus("@test.magi", root);
  try {
    const target = bus.chats.forPeer(principal); const id = messageDelivery.send(bus, target.id, "offline", MAGI_CONTACT_ID);
    const message = bus.board("MessageDeliveryJob").peekInput(id)!.message_id;
    assert.deepEqual(bus.deliveries.retry(message, "offline", 100), { attempts: 1, nextAttemptAt: 1100, exhausted: false });
    bus.close(); bus = new Bus("@test.magi", root);
    assert.equal(bus.deliveries.get(message)?.attempts, 1);
    for (let i = 2; i < 8; i++) assert.equal(bus.deliveries.retry(message, "offline", 100).exhausted, false);
    assert.equal(bus.deliveries.retry(message, "offline", 100).exhausted, true);
  } finally { bus.close(); rmSync(root, { recursive: true, force: true }); }
});
