import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Bus, MAGI_CONTACT_ID, messageDelivery, prepareRich, richMetadata, readMessageAttachment, reactToMessage, reactionsFor, stableMessageId } from '@magi/bus';
import { PeerWorker, principalFor } from '../worker.js';
const attachment = { name: 'notes.txt', mime_type: 'text/plain', data_base64: Buffer.from('peer file').toString('base64') };
test('direct peer rich chat transfers bytes, threaded owner replies wake once, reactions do not', async () => {
 const root = mkdtempSync(join(tmpdir(), 'magi-peer-rich-'));
 const buses = [new Bus('@a.magi', join(root, 'a')), new Bus('@b.magi', join(root, 'b'))];
 const workers = buses.map(bus => new PeerWorker(bus));
 try {
  for (const worker of workers) await worker.start();
  for (let i=0;i<2;i++) buses[i].contacts.savePeer(buses[i].contacts.forPeer({ principal: principalFor(buses[1-i].identity.publicKey), endpoint: workers[1-i].origin! }).id);
  const [a,b] = buses; const room = a.chats.forPeer(principalFor(b.identity.publicKey));
  const prepared = prepareRich(a, room.id, 'hello @b.magi', { attachments: [attachment] });
  const job = messageDelivery.send(a, room.id, prepared.content, MAGI_CONTACT_ID, 'webapp', { rich: prepared.rich });
  await workers[0].poll();
  assert.equal(a.board('MessageDeliveryJob').result(job)?.status, 'completed');
  const incomingRoom = b.chats.forPeer(principalFor(a.identity.publicKey), room.conversation_uuid!); const incoming = b.messages.list(incomingRoom.id)[0];
  assert.ok(incoming.content.startsWith('hello @b.magi'));
  const stored = richMetadata(b, incoming.id).attachments![0];
  assert.equal(readMessageAttachment(b, incomingRoom.id, incoming.id, stored.id).data_base64, attachment.data_base64);
  assert.ok(!incoming.content.includes(prepared.rich.attachments![0].path));
  const response = prepareRich(b, incomingRoom.id, 'threaded owner reply', { reply_to: stableMessageId(b, incoming.id) });
  messageDelivery.send(b, incomingRoom.id, response.content, MAGI_CONTACT_ID, 'webapp', { rich: response.rich });
  await workers[1].poll();
  const reply = a.messages.list(room.id).at(-1)!;
  assert.equal(reply.content, 'threaded owner reply'); assert.ok(a.deliveries.get(reply.id)?.job_id);
  reactToMessage(a, room.id, principalFor(a.identity.publicKey), reply.id, '👍'); await workers[0].poll();
  assert.equal(b.messages.list(incomingRoom.id).length, 2);
  assert.equal(reactionsFor(b, incomingRoom.id, stableMessageId(a, reply.id), principalFor(b.identity.publicKey))[0].mine, false);
  reactToMessage(b, incomingRoom.id, principalFor(b.identity.publicKey), stableMessageId(a, reply.id), '👍'); await workers[1].poll();
  assert.equal(reactionsFor(a, room.id, stableMessageId(a, reply.id), principalFor(a.identity.publicKey)).length, 2);
  reactToMessage(a, room.id, principalFor(a.identity.publicKey), reply.id, '👍', true); await workers[0].poll();
  assert.equal(reactionsFor(b, incomingRoom.id, stableMessageId(a, reply.id), '').length, 1);
 } finally { for (const worker of workers) await worker.stop(); for (const bus of buses) bus.close(); rmSync(root, { recursive:true,force:true }); }
});
test('group attachments and reactions replicate and survive out-of-order duplicate delivery', () => {
 const root = mkdtempSync(join(tmpdir(), 'magi-group-rich-')); const a = new Bus('@a.magi', join(root,'a')); const b = new Bus('@b.magi',join(root,'b'));
 try {
  const pa=principalFor(a.identity.publicKey), pb=principalFor(b.identity.publicKey);
  const group=a.groups.create({ title:'rich', endpoint:'http://localhost:4001',members:[{principal:pb,endpoint:'http://localhost:4002'}] }); b.groups.offerMembership(a.groups.membership(group.conversation_uuid));
  const event=a.groups.append(group.conversation_uuid,'hello',undefined,undefined,{attachments:[attachment]}); const received=b.groups.ingest(event.event);
  const stored=richMetadata(b,received.message_id).attachments![0]; assert.equal(readMessageAttachment(b,b.groups.get(group.conversation_uuid)!.chat_id,received.message_id,stored.id).data_base64,attachment.data_base64);
  const reaction={messageId:event.event.payload.eventId,emoji:'🎉',sequence:2,remove:true};
  const remove=a.groups.append(group.conversation_uuid,'',undefined,undefined,{reaction});
  const add=a.groups.append(group.conversation_uuid,'',undefined,undefined,{reaction:{...reaction,sequence:1,remove:false}});
  b.groups.ingest(remove.event); b.groups.ingest(add.event); b.groups.ingest(add.event);
  assert.deepEqual(reactionsFor(b,b.groups.get(group.conversation_uuid)!.chat_id,event.event.payload.eventId,pa),[]);
  assert.equal(b.messages.list(b.groups.get(group.conversation_uuid)!.chat_id).length,1);
 } finally {a.close();b.close();rmSync(root,{recursive:true,force:true});}
});
