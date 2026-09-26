/**
 * One message, one job, both directions.
 *
 * The message book is the source of truth: it holds the author, chat, and text. The
 * author decides who acts on it: what anyone else said is this MAGI's to answer, its
 * own words are for the chat's channel to deliver. A job carries only that row's id.
 *
 * `send` owns persistence: the row is written here, and the job only wakes whoever must
 * act. Workers read the message back from the book, never from the payload, so the
 * payload is a wake-up call and not a second copy of the conversation; the channel posts
 * exactly what was recorded. No caller writes the message book itself.
 */

import type { Bus } from "../bus.js";
import { SYSTEM_CONTACT_ID } from "../books/contactBook.js";

export type MessageDeliveryJob = {
  message_id: number;
};

/** Record the message, then hand it to whoever must act on it. Returns the job id. */
function send(bus: Bus, chat_id: number, text: string, contact_id = SYSTEM_CONTACT_ID, publisher = "channel"): number {
  if (!bus.chats.get(chat_id)) throw new Error(`chat ${chat_id} does not exist`);
  const message_id = bus.messages.add(chat_id, contact_id, text);
  return bus.board("MessageDeliveryJob").publish({ message_id }, publisher);
}

/** Keep it as history only: it was asked of someone else, so nobody delivers it. */
function record(bus: Bus, chat_id: number, text: string, contact_id?: number): void {
  bus.messages.add(chat_id, contact_id ?? SYSTEM_CONTACT_ID, text);
}

export const messageDelivery = { send, record };
