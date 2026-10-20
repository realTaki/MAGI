import assert from "node:assert/strict";
import test from "node:test";
import { generateKeyPairSync, privateDecrypt, constants } from "node:crypto";
import { listContacts, listConversations, listPeerEvents, getPeerProfile, sendPeerMessage, encryptSecret } from "../web/src/peer.ts";
import { peerMessages, contactLabel, conversationContact } from "../web/src/peer-messages.ts";
import { providerCard } from "@magi/bus/jobs/card.js";

const contact = { id: "shell", handle: "@shell.magi", name: "Shell", nickname: "", url: "http://localhost:1234", local: true };
const owner = { handle: contact.handle, public_key: null, owner: true };
const card = providerCard(contact.handle);
const event = (id: number, metadata?: Record<string, unknown>, role: "assistant" | "user" = "assistant") => ({ id, content: "hello", role, metadata, created_at: new Date(0).toISOString() });

test("renderer talks only to the local peer bridge with exact contact IDs", async () => {
  const calls: unknown[] = [];
  const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: { magiDesktop: { invokeLocal: async (method: string, payload: unknown) => { calls.push([method, payload]); return method === "peer.contacts" ? [contact] : method === "peer.profile" ? owner : []; } } } });
  try {
    assert.deepEqual(await listContacts(), [contact]);
    await listConversations();
    await listPeerEvents("shell");
    assert.equal((await getPeerProfile("shell")).owner, true);
    await sendPeerMessage("shell", "create a local EVA");
    await sendPeerMessage("shell", "", { local_eva_action: { contact_id: "2", action: "stop" } });
    assert.deepEqual(calls, [
      ["peer.contacts", undefined], ["peer.conversations", undefined], ["peer.events", { id: "shell" }], ["peer.profile", { id: "shell" }],
      ["peer.send", { id: "shell", content: "create a local EVA" }],
      ["peer.send", { id: "shell", content: "", metadata: { local_eva_action: { contact_id: "2", action: "stop" } } }],
    ]);
    Object.defineProperty(globalThis, "window", { configurable: true, value: {} });
    await assert.rejects(listContacts(), /local Shell connection/);
  } finally {
    if (previous) Object.defineProperty(globalThis, "window", previous);
    else Reflect.deleteProperty(globalThis, "window");
  }
});

test("only owner local history creates actionable cards; remote metadata cannot impersonate owner", () => {
  const events = [event(1, { card }), event(2, { card })];
  assert.equal(peerMessages(contact, events, owner).length, 1);
  assert.equal(peerMessages(contact, events, owner)[0].type, "form");
  for (const [peer, profile] of [
    [{ ...contact, local: false }, owner],
    [contact, { ...owner, owner: false }],
    [contact, { ...owner, handle: "@other.magi" }],
    [contact, undefined],
  ] as const) assert.ok(peerMessages(peer, events, profile).every((message) => message.type === "bot"));
  assert.equal(peerMessages(contact, [event(1, { card }, "user")], owner)[0].type, "user");
  assert.equal(peerMessages(contact, [event(1, { card: { ...card, handle: "@other.magi" } })], owner)[0].type, "bot");
});

test("owner receipts stay attached to cards and control submissions stay out of chat", () => {
  const events = [event(1, { card }), event(2, { card_result: { id: card.id, attempt: "attempt" } }, "user"), event(3, { card_status: { id: card.id, attempt: "attempt", state: "saved", values: { provider: "openai", model: "test", api_key: "never-copy" } } })];
  const messages = peerMessages(contact, events, owner);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].type, "form");
  if (messages[0].type === "form") {
    assert.equal(messages[0].receipt?.state, "saved");
    assert.deepEqual(messages[0].card.values, { provider: "openai", model: "test" });
  }
  assert.equal(contactLabel(contact), "Shell");
  assert.equal(contactLabel({ ...contact, nickname: "My Shell" }), "My Shell");
});

test("provider secrets are encrypted to the target EVA key", async () => {
  const keys = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const publicKey = keys.publicKey.export({ type: "spki", format: "pem" }).toString();
  const cipher = await encryptSecret(publicKey, "synthetic-provider-secret");
  assert.ok(!cipher.includes("synthetic-provider-secret"));
  assert.equal(privateDecrypt({ key: keys.privateKey, padding: constants.RSA_PKCS1_OAEP_PADDING, oaepHash: "sha256" }, Buffer.from(cipher, "base64")).toString(), "synthetic-provider-secret");
});

test("shared conversation UUID and author identity survive group presentation without granting owner rights", () => {
  const id = "conversation:dfd096ec-b269-4869-8193-295e5298adaf";
  const group = conversationContact({ id, title: "Research", local: false, group: true, members: [{ principal: "magi:peer", endpoint: "https://peer.example" }] });
  assert.equal(group.id, id);
  const rows = peerMessages(group, [{ ...event(1, { card }), id: "66affcf7-e7e9-43ce-a773-bf137aab3109", sender: "magi:peer" }], { ...owner, handle: "" });
  assert.deepEqual(rows, [{ type: "bot", text: "hello", sender: "magi:peer" }]);
});
