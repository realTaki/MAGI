import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { Bus, chat, messageDelivery, richMetadata, stableMessageId, reactToMessage, reactionsFor, readMessageAttachment, toLLMMessage, MAGI_CONTACT_ID } from '../index.js';
const file = { name: '../note.txt', mime_type: 'text/plain', data_base64: Buffer.from('file content').toString('base64') };

test('owner reply and attachments persist, remain scoped, and project native descriptors', () => {
  const root = mkdtempSync(join(tmpdir(), 'magi-rich-')); let bus = new Bus('@rich.magi', root);
  try {
    const room = bus.chats.forChannel('shell-owner', 'owner');
    chat(bus, room.id, bus.userContactId, { content: 'hi @peer.magi' });
    const first = bus.messages.list(room.id)[0];
    chat(bus, room.id, bus.userContactId, { content: '', reply_to: first.id, attachments: [file] });
    const second = bus.messages.list(room.id)[1]; const rich = richMetadata(bus, second.id);
    assert.equal(rich.reply_to, first.id); assert.equal(rich.attachments![0].name, 'note.txt');
    assert.match(second.content, /path alone does not expose/); assert.ok(second.content.includes(rich.attachments![0].path));
    const llm = toLLMMessage(second, 'webapp', bus.deliveries.get(second.id)?.metadata, String(room.id));
    assert.equal(llm.role, 'user'); assert.equal(llm.attachments?.length, 1); assert.match(llm.content, /reply to message/);
    assert.equal(readMessageAttachment(bus, room.id, second.id, rich.attachments![0].id).data_base64, file.data_base64);
    const other = bus.chats.forChannel('shell-owner', 'other');
    assert.throws(() => chat(bus, other.id, bus.userContactId, { content: 'reply', reply_to: first.id }), /conversation/);
    assert.throws(() => readMessageAttachment(bus, other.id, second.id, rich.attachments![0].id), /conversation/);
    reactToMessage(bus, room.id, String(bus.userContactId), second.id, '👍');
    reactToMessage(bus, room.id, String(bus.userContactId), second.id, '👍');
    assert.equal(reactionsFor(bus, room.id, second.id, String(bus.userContactId)).length, 1);
    bus.close(); bus = new Bus('@rich.magi', root);
    assert.equal(readMessageAttachment(bus, room.id, second.id, rich.attachments![0].id).data_base64, file.data_base64);
    reactToMessage(bus, room.id, String(bus.userContactId), second.id, '👍', true);
    assert.deepEqual(reactionsFor(bus, room.id, second.id, String(bus.userContactId)), []);
  } finally { bus.close(); rmSync(root, { recursive: true, force: true }); }
});

test('peer attachment replay is idempotent; reaction add/remove do not wake or enter history', () => {
  const root = mkdtempSync(join(tmpdir(), 'magi-rich-peer-')); const bus = new Bus('@rich.magi', root);
  try {
    const input = { principal: 'a'.repeat(64), endpoint: 'https://peer.example', conversationUUID: randomUUID(), messageId: randomUUID(), content: 'hello @rich.magi', attachments: [file] };
    const receipt = messageDelivery.receivePeer(bus, input);
    assert.equal(messageDelivery.receivePeer(bus, input).duplicate, true);
    assert.equal(bus.messages.list(receipt.chat_id).length, 1);
    const rich = richMetadata(bus, receipt.message_id);
    assert.equal(readMessageAttachment(bus, receipt.chat_id, input.messageId, rich.attachments![0].id).data_base64, file.data_base64);
    assert.throws(() => messageDelivery.receivePeer(bus, { ...input, attachments: [{ ...file, name: 'different' }] }), /reused/);
    const react = (sequence: number, remove: boolean) => messageDelivery.receivePeer(bus, { ...input, messageId: randomUUID(), content: '', attachments: undefined, reaction: { messageId: input.messageId, emoji: '❤️', remove, sequence } });
    assert.equal(react(1, false).job_id, null);
    assert.equal(reactionsFor(bus, receipt.chat_id, input.messageId, input.principal)[0].mine, true);
    react(3, true); react(2, false);
    assert.deepEqual(reactionsFor(bus, receipt.chat_id, input.messageId, input.principal), []);
    assert.equal(bus.messages.list(receipt.chat_id).length, 1);
    assert.equal(bus.messages.observe(0).length, 1);
    const reply = messageDelivery.send(bus, receipt.chat_id, 'answer', MAGI_CONTACT_ID, 'agent', messageDelivery.replyMetadata(bus, receipt.message_id));
    assert.ok(stableMessageId(bus, bus.board('MessageDeliveryJob').peekInput(reply)!.message_id));
  } finally { bus.close(); rmSync(root, { recursive: true, force: true }); }
});

test('upgraded peers accept an identical pre-rich journal replay but reject new payloads', () => {
  const root = mkdtempSync(join(tmpdir(), 'magi-rich-legacy-')); const bus = new Bus('@rich.magi', root);
  try {
    const input = { principal: 'b'.repeat(64), endpoint: 'https://peer.example', conversationUUID: randomUUID(), messageId: randomUUID(), content: 'legacy' };
    const contact = bus.contacts.forPeer({ principal: input.principal, endpoint: input.endpoint });
    const room = bus.chats.forPeer(input.principal, input.conversationUUID);
    bus.deliveries.persist({ chat_id: room.id, contact_id: contact.id, content: input.content, publisher: 'peer', key: `peer:${input.principal}:${input.messageId}`, metadata: { peer: { messageId: input.messageId, conversationUUID: input.conversationUUID } } });
    assert.equal(messageDelivery.receivePeer(bus, input).duplicate, true);
    assert.throws(() => messageDelivery.receivePeer(bus, { ...input, attachments: [file] }), /reused/);
  } finally { bus.close(); rmSync(root, { recursive: true, force: true }); }
});
