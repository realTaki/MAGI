/** Ordinary conversation adornments, never structured owner commands. */
import type { Bus } from './bus.js';
import { groupPrincipal } from './books/groupBook.js';
import { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID } from './books/contactBook.js';
import { messageDelivery } from './jobs/messageDelivery.js';
import { storeAttachments, attachmentText, readStoredAttachment, type AttachmentInput, type StoredAttachment } from './attachments.js';

export type Reaction = { messageId: string; emoji: string; remove: boolean; sequence: number };
export type RichMessage = { reply_to?: string | number; reply_excerpt?: string; attachments?: StoredAttachment[]; reaction?: Reaction };
export type RichInput = { reply_to?: string | number; attachments?: AttachmentInput[] };
export function richMetadata(bus: Bus, messageId: number): RichMessage {
  const metadata = JSON.parse(bus.deliveries.get(messageId)?.metadata ?? '{}');
  return { ...(metadata.peer?.replyTo ? { reply_to: metadata.peer.replyTo } : {}), ...(metadata.rich ?? {}) };
}
export function stableMessageId(bus: Bus, messageId: number): string | number {
  return JSON.parse(bus.deliveries.get(messageId)?.metadata ?? '{}').peer?.messageId ?? messageId;
}
export function findMessage(bus: Bus, chatId: number, id: string | number) {
  if (typeof id !== 'string' && typeof id !== 'number') throw new Error('invalid message reference');
  const numeric = Number(id);
  const local = Number.isSafeInteger(numeric) ? bus.messages.get(numeric) : undefined;
  const peer = typeof id === "string" ? bus.messages.findPeer(chatId, id) : undefined;
  const message = peer ?? (local?.chat_id === chatId ? local : bus.messages.findPeer(chatId, String(id)));
  if (!message || messageDelivery.isInternal(bus, message.id) || richMetadata(bus, message.id).reaction) throw new Error('message is not in this conversation');
  return message;
}
export function prepareRich(bus: Bus, chatId: number, content: string, input: RichInput): { content: string; rich: RichMessage } {
  if (typeof content !== 'string') throw new Error('chat content must be text');
  const rich: RichMessage = {};
  if (input.reply_to !== undefined) {
    const target = findMessage(bus, chatId, input.reply_to);
    rich.reply_to = stableMessageId(bus, target.id); rich.reply_excerpt = target.content.slice(0, 2000);
  }
  if (input.attachments !== undefined) rich.attachments = storeAttachments(bus.workspace, String(chatId), input.attachments);
  const suffix = rich.attachments?.length ? attachmentText(rich.attachments) : '';
  return { content: content + (suffix ? `\n\n${suffix}` : ''), rich };
}
export function validateReaction(value: unknown): asserts value is Reaction {
  const r = value as Reaction;
  if (!r || typeof r !== 'object' || Object.keys(r).some(k => !['messageId', 'emoji', 'remove', 'sequence'].includes(k)) || typeof r.messageId !== 'string' || !r.messageId || r.messageId.length > 256 || typeof r.emoji !== 'string' || !r.emoji || r.emoji.length > 32 || /[\p{Cc}\p{Cs}]/u.test(r.emoji) || !/\p{Extended_Pictographic}|\p{Regional_Indicator}|[0-9#*]\uFE0F?\u20E3/u.test(r.emoji) || typeof r.remove !== 'boolean' || !Number.isSafeInteger(r.sequence) || r.sequence < 1) throw new Error('invalid emoji reaction');
}
export function applyReaction(bus: Bus, chatId: number, actor: string, reaction: Reaction) {
  validateReaction(reaction);
  // A replicated event may arrive before its target. Keep the scoped operation;
  // projection only exposes it when that target is present in this conversation.
  const key = `reaction:${chatId}:${encodeURIComponent(reaction.messageId)}:${encodeURIComponent(actor)}:${encodeURIComponent(reaction.emoji)}`;
  const previous = JSON.parse(bus.settings.get(key) ?? 'null');
  if (!previous || reaction.sequence > previous.sequence) bus.settings.set(key, JSON.stringify(reaction));
}
export function reactionsFor(bus: Bus, chatId: number, messageId: string | number, self: string) {
  const prefix = `reaction:${chatId}:${encodeURIComponent(String(messageId))}:`;
  const latest = new Map<string, { actor: string; reaction: Reaction }>();
  const consider = (actor: string, reaction: Reaction) => {
    const key = `${actor}\0${reaction.emoji}`;
    if (!latest.has(key) || latest.get(key)!.reaction.sequence < reaction.sequence) latest.set(key, { actor, reaction });
  };
  for (const { key, value } of bus.settings.withPrefix(prefix)) {
    consider(decodeURIComponent(key.slice(prefix.length).split(':')[0]), JSON.parse(value) as Reaction);
  }
  // The delivery journal is authoritative across the crash window between a
  // durable message commit and its settings projection (including outgoing ops).
  for (const message of bus.messages.reactionEvents(chatId, String(messageId))) {
    const reaction = richMetadata(bus, message.id).reaction;
    const actor = message.contact_id === MAGI_CONTACT_ID ? groupPrincipal(bus.identity.publicKey) : bus.contacts.get(message.contact_id)?.peer_principal;
    if (reaction && actor) consider(actor, reaction);
  }
  return [...latest.values()].filter(({ reaction }) => !reaction.remove).map(({ actor, reaction }) => ({ actor, emoji: reaction.emoji, mine: actor === self }));
}
export function reactToMessage(bus: Bus, chatId: number, actor: string, messageId: string | number, emoji: string, remove = false) {
  const target = findMessage(bus, chatId, messageId);
  const sequenceKey = `reaction-sequence:${actor}`;
  const reaction: Reaction = { messageId: String(stableMessageId(bus, target.id)), emoji, remove, sequence: Number(bus.settings.get(sequenceKey) ?? 0) + 1 };
  validateReaction(reaction);
  bus.settings.set(sequenceKey, String(reaction.sequence));
  const channel = bus.chats.get(chatId)?.channel;
  if (channel === 'peer' || channel === 'group') messageDelivery.send(bus, chatId, '', MAGI_CONTACT_ID, 'webapp', { rich: { reaction } });
  applyReaction(bus, chatId, actor, reaction);
  return { accepted: true };
}
export function readMessageAttachment(bus: Bus, chatId: number, messageId: string | number, attachmentId: string) {
  const message = findMessage(bus, chatId, messageId);
  const attachment = richMetadata(bus, message.id).attachments?.find(a => a.id === attachmentId);
  if (!attachment) throw new Error('attachment is not in this message');
  return { name: attachment.name, mime_type: attachment.mime_type, data_base64: readStoredAttachment(bus.workspace, String(chatId), attachment).toString('base64') };
}
export function visibleMessage(bus: Bus, messageId: number): boolean {
  return bus.messages.get(messageId)?.contact_id !== SYSTEM_CONTACT_ID && !messageDelivery.isInternal(bus, messageId) && !richMetadata(bus, messageId).reaction;
}
