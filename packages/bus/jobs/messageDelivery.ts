/**
 * One message, one job, both directions.
 *
 * The message book is the source of truth: it holds the author, chat, and text. The
 * author decides who acts on it: what anyone else said is this MAGI's to answer, its
 * own words are for the chat's channel to deliver. A job carries that row's id and,
 * when the message has structured fields, those fields too.
 *
 * `send` owns persistence: the row is written here, and the job only wakes whoever must
 * act. Workers read the message back from the book, never from the payload, so the
 * payload is a wake-up call and not a second copy of the conversation; the channel posts
 * exactly what was recorded. No caller writes the message book itself.
 */

import { randomUUID } from "node:crypto";
import { chat } from "../chat.js";
import type { Bus } from "../bus.js";
import { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID } from "../books/contactBook.js";

export type MessageDeliveryJob = {
  message_id: number;
  /** Structured fields that ride with the message and are not the message text. */
  metadata?: Record<string, unknown>;
};

/** Record the message, then hand it to whoever must act on it. Returns the job id. */
function send(bus: Bus, chat_id: number, text: string, contact_id = SYSTEM_CONTACT_ID, publisher = "channel", metadata?: Record<string, unknown>): number {
  const target = bus.chats.get(chat_id);
  if (!target) throw new Error(`chat ${chat_id} does not exist`);
  if (target.channel === "group" && contact_id === MAGI_CONTACT_ID) {
    const replyTo = (metadata?.peer as { replyTo?: string } | undefined)?.replyTo;
    return bus.groups.append(target.delivery_address, text, replyTo).job_id!;
  }
  const peer = target.channel === "peer" && contact_id === MAGI_CONTACT_ID
    ? { messageId: randomUUID(), conversationUUID: target.conversation_uuid, ...((metadata?.peer as Record<string, unknown> | undefined) ?? {}) } : undefined;
  const stored = bus.deliveries.persist({ chat_id, contact_id, content: text, publisher, metadata: peer ? { ...metadata, peer } : metadata });
  return bus.deliveries.enqueue(bus, stored.delivery)!;
}

/** Keep it as history only: it was asked of someone else, so nobody delivers it. */
function record(bus: Bus, chat_id: number, text: string, contact_id?: number): void {
  bus.messages.add(chat_id, contact_id ?? SYSTEM_CONTACT_ID, text);
}

/** The chat the operator chose for notices, or null before they have spoken. */
function homeChat(bus: Bus): number | null {
  const stored = bus.settings.get("home.chat_id");
  const id = stored === null ? Number.NaN : Number(stored);
  return Number.isInteger(id) && bus.chats.get(id) !== null ? id : null;
}

/** Persist the operator's chosen notice destination. Callers validate the chat first. */
function setHomeChat(bus: Bus, chat_id: number): void {
  bus.settings.set("home.chat_id", String(chat_id));
}

/** Deliver a system notice to its chat, or fall back to the operator's home chat. */
function notify(bus: Bus, text: string, chat_id?: number): number | null {
  const target = chat_id ?? homeChat(bus);
  return target === null ? null : send(bus, target, text, MAGI_CONTACT_ID, "agent");
}

/**
 * The ASP chat a card is shown in: the operator's home chat, once this MAGI
 * has one. A startup with no home chat opens one instead of borrowing an
 * unrelated invite.
 */
function presentChat(bus: Bus): number | null {
  const home = homeChat(bus);
  const chat = home === null ? null : bus.chats.get(home);
  if ((chat?.channel === "asp" || chat?.channel === "shell-owner" || chat?.channel === "local-owner") && chat.delivery_address) return chat.id;
  return null;
}

export type PeerInbound = { principal: string; endpoint: string; conversationUUID: string; messageId: string; content: string; replyTo?: string };
export type PeerReceipt = { job_id: number | null; message_id: number; duplicate: boolean; chat_id: number };

/** Authenticated adapter only. The shared chat boundary validates text and sender. */
function receivePeer(bus: Bus, input: PeerInbound): PeerReceipt {
  if (typeof input.messageId !== "string" || !input.messageId || input.messageId.length > 256) throw new Error("invalid message ID");
  if (input.replyTo !== undefined && (typeof input.replyTo !== "string" || !input.replyTo || input.replyTo.length > 256)) throw new Error("invalid reply ID");
  const contact = bus.contacts.forPeer({ principal: input.principal, endpoint: input.endpoint });
  const target = bus.chats.forPeer(input.principal, input.conversationUUID);
  const result = chat(bus, target.id, contact.id, { content: input.content }, "peer", input);
  if (!("message_id" in result)) throw new Error("peer receipt missing");
  bus.chatMembers.add(target.id, contact.id);
  return result as PeerReceipt;
}

/** Correlate every agent response, including errors and tool narration. */
function replyMetadata(bus: Bus, messageId?: number): Record<string, unknown> | undefined {
  const delivery = messageId === undefined ? undefined : bus.deliveries.get(messageId);
  const metadata = delivery?.metadata ? JSON.parse(delivery.metadata) as { peer?: { messageId?: string } } : undefined;
  return metadata?.peer?.messageId ? { peer: { replyTo: metadata.peer.messageId } } : undefined;
}

export const messageDelivery = { send, record, homeChat, setHomeChat, notify, presentChat, receivePeer, replyMetadata };
