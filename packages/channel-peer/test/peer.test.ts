import test from "node:test";
import { createServer } from "node:http";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Bus, MAGI_CONTACT_ID, messageDelivery } from "@magi/bus";
import { PeerWorker } from "../worker.js";
import { PeerClient, discoverPeer } from "../client.js";
import { PeerServer } from "../server.js";
import { decodeEnvelope, encodeEnvelope, parseMessage, peerURL, principalFor, type PeerMessage } from "../protocol.js";

function fixture() {
  const path = mkdtempSync(join(tmpdir(), "magi-peer-"));
  const a = new Bus("@a.magi", join(path, "a"));
  const b = new Bus("@b.magi", join(path, "b"));
  return { a, b, dispose() { a.close(); b.close(); rmSync(path, { recursive: true, force: true }); } };
}
function message(endpoint: string): PeerMessage { return { messageId: randomUUID(), conversationUUID: randomUUID(), content: "hello", reachableURL: endpoint, handle: "@user" }; }

test("direct peers durably accept, deduplicate after restart, and never grant body handles owner authority", async () => {
  const f = fixture();
  let b = new PeerWorker(f.b);
  try {
    await b.start();
    const card = await discoverPeer(b.origin!);
    assert.equal(card.principal, principalFor(f.b.identity.publicKey));
    const input = message("http://127.0.0.1:47777");
    const client = new PeerClient(f.a.identity);
    assert.equal((await client.send(b.origin!, card.principal, input)).duplicate, false);
    assert.equal((await client.send(b.origin!, card.principal, input)).duplicate, true);
    const contacts = f.b.contacts.list().filter(contact => contact.peer_id);
    assert.equal(contacts.length, 1);
    assert.equal(contacts[0].role, "stranger");
    assert.equal(contacts[0].peer_trust, "accepted");
    assert.notEqual(contacts[0].id, 1);
    const origin = b.origin;
    await b.stop();
    b = new PeerWorker(f.b);
    await b.start();
    assert.equal(b.origin, origin);
    // The HTTP pool may still hold the socket closed by restart; the durable
    // delivery worker retries the same ID on this expected transport failure.
    const receipt = await client.send(b.origin!, card.principal, input).catch(() => client.send(b.origin!, card.principal, input));
    assert.equal(receipt.duplicate, true);
    assert.equal(f.b.messages.list(f.b.chats.forPeer(client.principal, input.conversationUUID).id).length, 1);
    await assert.rejects(client.send(b.origin!, card.principal, { ...input, content: "different content" }));
  } finally { await b.stop(); f.dispose(); }
});

test("ordinary chat and reply cross two independent servers without a relay or reply loops", async () => {
  const f = fixture();
  const a = new PeerWorker(f.a); const b = new PeerWorker(f.b);
  try {
    await a.start(); await b.start();
    const pa = principalFor(f.a.identity.publicKey), pb = principalFor(f.b.identity.publicKey);
    f.a.contacts.acceptPeer(f.a.contacts.forPeer({ principal: pb, endpoint: b.origin! }).id);
    f.b.contacts.acceptPeer(f.b.contacts.forPeer({ principal: pa, endpoint: a.origin! }).id);
    const chat = f.a.chats.forPeer(pb);
    const out = messageDelivery.send(f.a, chat.id, "hello peer", MAGI_CONTACT_ID);
    assert.equal(await a.poll(), true);
    assert.equal(f.a.board("MessageDeliveryJob").result(out)?.status, "completed");
    const bchat = f.b.chats.forPeer(pa, chat.conversation_uuid!);
    const inbound = f.b.messages.list(bchat.id)[0];
    const reply = messageDelivery.send(f.b, bchat.id, "hello back", MAGI_CONTACT_ID, "agent", messageDelivery.replyMetadata(f.b, inbound.id));
    assert.equal(await b.poll(), true);
    assert.equal(f.b.board("MessageDeliveryJob").result(reply)?.status, "completed");
    const received = f.a.messages.list(chat.id).find(row => row.content === "hello back")!;
    assert.ok(received);
    assert.equal(f.a.deliveries.get(received.id)?.enqueue, false);
  } finally { await a.stop(); await b.stop(); f.dispose(); }
});

test("identity labels route plain messages and private HTTP origins are supported", () => {
  const f = fixture();
  try {
    const audience = principalFor(f.b.identity.publicKey);
    const body = encodeEnvelope(f.a.identity, audience, "chat", message("http://localhost:1234"));
    assert.equal(decodeEnvelope(body, audience, "chat").principal, principalFor(f.a.identity.publicKey));
    assert.equal((decodeEnvelope(body.replace("hello", "updated"), audience, "chat").data as PeerMessage).content, "updated", "trusted network messages have no signature gate");
    assert.throws(() => decodeEnvelope(body, principalFor(f.a.identity.publicKey), "chat"), /routing/);
    assert.equal(JSON.parse(body).signature, undefined);
    for (const url of ["http://10.0.0.2:8000", "http://eva.default.svc.cluster.local:8000", "http://100.64.0.2:8000"]) assert.equal(peerURL(url).protocol, "http:");
    assert.throws(() => parseMessage({ ...message("http://localhost:1"), metadata: { card_result: {} } }), /ordinary/);
    for (const url of ["https://a.test/path", "https://user:pass@a.test", "https://a.test?token=secret", "file:///tmp/test"]) assert.throws(() => peerURL(url));
  } finally { f.dispose(); }
});

test("receipt is withheld when persistence fails; retry succeeds without claiming an earlier acceptance", async () => {
  const f = fixture(); let fail = true; let calls = 0;
  const server = new PeerServer(f.b.identity, f.b.handle, () => { calls++; if (fail) throw new Error("disk failure"); return { duplicate: false }; });
  try {
    await server.start();
    const client = new PeerClient(f.a.identity), input = message("http://localhost:1234");
    await assert.rejects(client.send(server.origin!, server.principal, input), /503/);
    fail = false;
    assert.equal((await client.send(server.origin!, server.principal, input)).accepted, true);
    assert.equal(calls, 2);
    assert.equal((await client.send(server.origin!, server.principal, { ...input, messageId: randomUUID(), content: "汉".repeat(32_000) })).accepted, true);
    await assert.rejects(client.send(server.origin!, client.principal, input), /400/);
  } finally { await server.stop(); f.dispose(); }
});

test("outbound failures retain persisted retry schedule and binding failures are visible", async () => {
  const f = fixture(); const worker = new PeerWorker(f.a);
  try {
    await worker.start();
    const duplicate = new PeerServer(f.b.identity, f.b.handle, () => ({ duplicate: false }), { port: Number(new URL(worker.origin!).port) });
    await assert.rejects(duplicate.start(), /EADDRINUSE/);
    const dead = new PeerServer(f.b.identity, f.b.handle, () => ({ duplicate: false }));
    await dead.start(); const endpoint = dead.origin!; await dead.stop();
    const principal = principalFor(f.b.identity.publicKey);
    f.a.contacts.acceptPeer(f.a.contacts.forPeer({ principal, endpoint }).id);
    const chat = f.a.chats.forPeer(principal);
    messageDelivery.send(f.a, chat.id, "retry me", MAGI_CONTACT_ID);
    await worker.poll();
    const outgoing = f.a.messages.list(chat.id)[0];
    const retry = f.a.deliveries.get(outgoing.id)!;
    assert.equal(retry.attempts, 1); assert.ok(retry.next_attempt_at > Date.now());
    assert.equal(await worker.poll(), false);
  } finally { await worker.stop(); f.dispose(); }
});


test("trusted private-network proxy sends chat directly without a verification handshake", async () => {
  const f = fixture(); const real = new PeerServer(f.b.identity, f.b.handle, () => ({ duplicate: false }));
  const requests: string[] = [];
  const proxy = createServer(async (request, response) => {
    const chunks: Buffer[] = []; for await (const chunk of request) chunks.push(Buffer.from(chunk));
    requests.push(request.url!);
    const forwarded = await fetch(new URL(request.url!, real.origin!), { method: request.method, headers: { "Content-Type": "application/json" }, body: Buffer.concat(chunks) });
    response.writeHead(forwarded.status, { "Content-Type": "application/json" }); response.end(await forwarded.text());
  });
  try {
    await real.start(); await new Promise<void>(resolve => proxy.listen(0, "127.0.0.1", resolve));
    const address = proxy.address(); assert.ok(address && typeof address !== "string");
    assert.equal((await new PeerClient(f.a.identity).send(`http://127.0.0.1:${address.port}`, real.principal, message("http://localhost:1234"))).accepted, true);
    assert.deepEqual(requests, ["/chat"]);
  } finally { await new Promise<void>(resolve => { proxy.close(() => resolve()); proxy.closeAllConnections(); }); await real.stop(); f.dispose(); }
});
