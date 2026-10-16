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

import type { Bus } from "../bus.js";
import { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID } from "../books/contactBook.js";

export type MessageDeliveryJob = {
  message_id: number;
  /** Structured fields that ride with the message and are not the message text. */
  metadata?: Record<string, unknown>;
};

/** Record the message, then hand it to whoever must act on it. Returns the job id. */
function send(bus: Bus, chat_id: number, text: string, contact_id = SYSTEM_CONTACT_ID, publisher = "channel", metadata?: Record<string, unknown>): number {
  if (!bus.chats.get(chat_id)) throw new Error(`chat ${chat_id} does not exist`);
  const message_id = bus.messages.add(chat_id, contact_id, text);
  // The metadata is on the job before it is visible, so a restart still has it.
  const input: MessageDeliveryJob = metadata ? { message_id, metadata } : { message_id };
  return bus.board("MessageDeliveryJob").publish(input, publisher);
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
  if (chat?.channel === "asp" && chat.delivery_address) return chat.id;
  return null;
}

export const messageDelivery = { send, record, homeChat, setHomeChat, notify, presentChat };
