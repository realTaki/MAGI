import { attachmentText, wireAttachments, type AttachmentInput } from "../attachments.js";
import type { RichMessage, Reaction } from "../richMessage.js";
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

/** Internal analysis may inform a foreground turn without appearing in its UI. */
function isInternal(bus: Bus, messageId: number): boolean {
  const delivery = bus.deliveries.get(messageId);
  return bus.messages.get(messageId)?.contact_id === SYSTEM_CONTACT_ID || delivery?.publisher === "subconscious";
}

/** Module diagnostics are local system inputs, not outgoing MAGI messages. */
function isError(bus: Bus, messageId?: number): boolean {
  if (messageId === undefined) return false;
  const delivery = bus.deliveries.get(messageId);
  return bus.messages.get(messageId)?.contact_id === SYSTEM_CONTACT_ID && delivery?.publisher.startsWith("error:") === true;
}
function reportError(bus: Bus, text: string, options: { module: string; source?: string; chat_id?: number; wake?: boolean }): number | null {
  const chat_id = options.chat_id ?? homeChat(bus) ?? bus.chats.forChannel("internal", "system").id;
  // Diagnostics can contain provider/server text. Never persist known secrets or
  // URL credentials/authorization values as chat history.
  let safe = text;
  for (const name of bus.secrets.names()) { const secret = bus.secrets.get(name); if (secret) safe = safe.split(secret).join("[redacted]"); }
  safe = safe.replace(/(authorization\s*[:=]\s*)(?:bearer\s+|basic\s+)?[^\s,;]+/gi, "$1[redacted]")
    .replace(/https?:\/\/[^\s<>]+/gi, value => { try { const url = new URL(value); url.username = ""; url.password = ""; if (url.search) url.search = "?redacted"; return url.toString(); } catch { return "[redacted URL]"; } });
  const stored = bus.deliveries.persist({ chat_id, contact_id: SYSTEM_CONTACT_ID, content: safe,
    publisher: `error:${options.module}`, key: options.source ? `system-error:${options.module}:${options.source}` : undefined,
    enqueue: options.wake !== false });
  return bus.deliveries.enqueue(bus, stored.delivery);
}

/** Record the message, then hand it to whoever must act on it. Returns the job id. */
function send(bus: Bus, chat_id: number, text: string, contact_id = SYSTEM_CONTACT_ID, publisher = "channel", metadata?: Record<string, unknown>): number {
  const target = bus.chats.get(chat_id);
  if (!target) throw new Error(`chat ${chat_id} does not exist`);
  if (target.channel === "subconscious" && (contact_id !== SYSTEM_CONTACT_ID || publisher !== "task")) {
    throw new Error("subconscious accepts only internal scheduled continuations");
  }
  if (target.channel === "group" && contact_id === MAGI_CONTACT_ID) {
    const rich = metadata?.rich as RichMessage | undefined;
    const replyTo = (metadata?.peer as { replyTo?: string } | undefined)?.replyTo ?? (rich?.reply_to === undefined ? undefined : String(rich.reply_to));
    const suffix = rich?.attachments?.length ? `\n\n${attachmentText(rich.attachments)}` : "";
    const content = suffix && text.endsWith(suffix) ? text.slice(0, -suffix.length) : text;
    return bus.groups.append(target.delivery_address, content, replyTo, typeof metadata?.system_error_source === "number" ? metadata.system_error_source : undefined, { attachments: rich?.attachments ? wireAttachments(bus.workspace, String(chat_id), rich.attachments) : undefined, reaction: rich?.reaction, requestReply: rich?.reply_to !== undefined }).job_id!;
  }
  const peer = target.channel === "peer" && contact_id === MAGI_CONTACT_ID
    ? { messageId: randomUUID(), conversationUUID: target.conversation_uuid, ...((metadata?.peer as Record<string, unknown> | undefined) ?? {}) } : undefined;
  const stored = bus.deliveries.persist({ chat_id, contact_id, content: text, publisher, metadata: peer ? { ...metadata, peer } : metadata });
  return bus.deliveries.enqueue(bus, stored.delivery)!;
}

/** An agent's output stays private in its internal conversation, including errors. */
function respond(bus: Bus, chat_id: number, text: string, metadata?: Record<string, unknown>): number | null {
  if (["subconscious", "internal"].includes(bus.chats.get(chat_id)?.channel ?? "")) {
    record(bus, chat_id, text, MAGI_CONTACT_ID);
    return null;
  }
  return send(bus, chat_id, text, MAGI_CONTACT_ID, "agent", metadata);
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
  if (["subconscious", "internal"].includes(bus.chats.get(chat_id)?.channel ?? "")) throw new Error("an internal conversation cannot be home");
  bus.settings.set("home.chat_id", String(chat_id));
}

/** Deliver a system notice to its chat, or fall back to the operator's home chat. */
function notify(bus: Bus, text: string, chat_id?: number): number | null {
  const target = chat_id ?? homeChat(bus);
  return target === null ? null : send(bus, target, text, MAGI_CONTACT_ID, "agent");
}

/**
 * The local owner chat a card is shown in: the operator's home chat, once this MAGI
 * has one. A startup with no home chat opens one instead of borrowing an
 * unrelated invite.
 */
function presentChat(bus: Bus): number | null {
  const home = homeChat(bus);
  const chat = home === null ? null : bus.chats.get(home);
  if ((chat?.channel === "shell-owner" || chat?.channel === "local-owner") && chat.delivery_address) return chat.id;
  return null;
}

export type PeerInbound = { principal: string; endpoint: string; conversationUUID: string; messageId: string; content: string; replyTo?: string; requestReply?: boolean; attachments?: AttachmentInput[]; reaction?: Reaction };
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
  const peer = metadata?.peer?.messageId ? { peer: { replyTo: metadata.peer.messageId } } : {};
  return isError(bus, messageId) ? { ...peer, system_error_source: messageId } : Object.keys(peer).length ? peer : undefined;
}

export const messageDelivery = { send, respond, record, isInternal, isError, reportError, homeChat, setHomeChat, notify, presentChat, receivePeer, replyMetadata };
