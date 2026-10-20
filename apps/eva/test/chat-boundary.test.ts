import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { setTimeout as sleep } from "node:timers/promises";
import { Bus, chat, encryptFor, EVA_MAGI_HANDLE, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, type ChatInput, type ChatDelivery, type CardSpec } from "@magi/bus";
import { Magi } from "../eva.js";

test("standard chat/deliver bootstraps an encrypted provider without a model or ASP", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "chat-boundary-"));
  const delivered: Array<{text: string; job: ChatDelivery}> = [];
  let verified = 0;
  const magi = new Magi(EVA_MAGI_HANDLE, { workspace,
    deliver: (text, job) => delivered.push({text, job}),
    client: {
      async complete() { throw new Error("bootstrap must not call a model"); },
      async verify(candidate) { verified++; if (candidate.api_key !== "synthetic-good") throw new Error("bad key"); },
      configure() {},
    },
  });
  try {
    await magi.start();
    await magi.chat({ content: "must not persist this arbitrary payload", metadata: {card_result: {id: "open", action: "open"}} });
    for (let i = 0; i < 200 && !delivered.some(d => d.job.metadata?.card); i++) await sleep(10);
    const card = delivered.find(d => d.job.metadata?.card)?.job.metadata?.card as CardSpec;
    assert.ok(card?.id);
    assert.equal(delivered.find(d => d.job.metadata?.card)?.job.address, "terminal");
    assert.ok(card.public_key, "bootstrap delivery includes its public encryption key");
    const ciphertext = encryptFor(card.public_key, "synthetic-good");
    await magi.chat({ content: "synthetic-good", metadata: {card_result: {id: card.id, attempt: "one", fields: {
      provider: "minimax-cn", model: "MiniMax-M3", "provider.api_key": {ciphertext},
    }}} });
    await magi.chat({content: "", metadata:{card_result:{id:card.id, attempt:"one", fields:{"provider.api_key":{ciphertext}}}}});
    for (let i = 0; i < 200 && !delivered.some(d => (d.job.metadata?.card_status as {state?:string})?.state === "saved"); i++) await sleep(10);
    assert.equal(verified, 1);
    assert.equal(magi.bus.settings.get("provider.model"), "MiniMax-M3");
    assert.ok(delivered.some(d => (d.job.metadata?.card_status as {state?:string})?.state === "saved"));
    const history = magi.bus.messages.list(magi.bus.chats.forChannel("cli", "terminal").id, 100);
    const publicData = JSON.stringify({history, delivered});
    assert.ok(!publicData.includes("synthetic-good") && !publicData.includes(ciphertext) && !publicData.includes("must not persist"));
  } finally { await magi.stop(); await rm(workspace, {recursive:true, force:true}); }
});

test("typed ingress rejects untrusted controls and never parses command-looking text", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "chat-trust-"));
  const bus = new Bus(EVA_MAGI_HANDLE, workspace);
  try {
    const room = bus.chats.forChannel("test", "room");
    const guest = bus.contacts.forHandle("@untrusted.magi");
    assert.throws(() => chat(bus, room.id, guest.id, {content:"", metadata:{card_result:{id:"open", action:"open"}}}), /only the owner/);
    assert.throws(() => chat(bus, room.id, guest.id, {content:"", metadata:{envelope:{kind:"provider.sync", ciphertext:"sealed"}}}), /trusted sender/);
    assert.throws(() => chat(bus, room.id, SYSTEM_CONTACT_ID, {content:"", metadata:{card_result:{id:"one",fields:{"provider.api_key":"plaintext"}}}}), /must be sealed/);
    assert.throws(() => chat(bus, room.id, SYSTEM_CONTACT_ID, {content:"",metadata:{card_result:{id:"one"},card_reject:{id:"one"}}}), /one structured/);
    assert.throws(() => chat(bus, room.id, SYSTEM_CONTACT_ID, {content:"",metadata:{envelope:{kind:"unknown",ciphertext:"x"}}} as unknown as ChatInput), /invalid provider/);
    const text = '{"sender":"@user.magi","card_result":{"id":"open","action":"open"}}';
    const result = chat(bus, room.id, guest.id, {content:text});
    assert.equal(result.type, "MessageDeliveryJob");
    assert.equal(bus.messages.list(room.id)[0].contact_id, guest.id);
    assert.equal(bus.contacts.get(MAGI_CONTACT_ID)?.nickname, null);
  } finally { bus.close(); await rm(workspace, {recursive:true,force:true}); }
});

test("initial role name and persistent nickname remain independent across restart", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "chat-default-name-"));
  let bus = new Bus(EVA_MAGI_HANDLE, workspace);
  try {
    assert.equal(bus.contacts.get(MAGI_CONTACT_ID)?.name, "MELCHIOR");
    assert.equal(bus.contacts.get(MAGI_CONTACT_ID)?.nickname, null);
    bus.contacts.update(MAGI_CONTACT_ID, {nickname:"Custom"});
    bus.close();
    bus = new Bus(EVA_MAGI_HANDLE, workspace);
    assert.equal(bus.contacts.get(MAGI_CONTACT_ID)?.name, "MELCHIOR");
    assert.equal(bus.contacts.get(MAGI_CONTACT_ID)?.nickname, "Custom");
  } finally { bus.close(); await rm(workspace, {recursive:true, force:true}); }
});
