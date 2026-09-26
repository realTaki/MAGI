/**
 * One message, one job, both directions.
 *
 * `contact_id` is the author, and the author decides who acts on it: what anyone else
 * said is this MAGI's to answer, its own words are for the chat's channel to deliver.
 * Nothing else travels with the job — a channel reads its own name and address off the
 * chat row, so a delivery cannot point somewhere the chat does not.
 *
 * `send` owns persistence: the row is written here, and the job only wakes whoever must
 * act. The agent reads the message back from the book, never from the payload, so the
 * payload is a wake-up call and not a second copy of the conversation; the channel posts
 * exactly what was recorded. No caller writes the message book itself.
 */

import type { Bus } from "../bus.js";
import { SYSTEM_CONTACT_ID } from "../books/contactBook.js";

export type MessageDeliveryJob = {
  chat_id: number;
  text: string;
  /** Who said it. Absent means the operator — the unnamed speaker of every channel. */
  contact_id?: number;
};

/** Record the message, then hand it to whoever must act on it. Returns the job id. */
function send(bus: Bus, input: MessageDeliveryJob, publisher = "channel"): number {
  const chat = bus.chats.get(input.chat_id);
  if (!chat) throw new Error(`chat ${input.chat_id} does not exist`);
  const contactId = input.contact_id ?? SYSTEM_CONTACT_ID;
  bus.messages.add(input.chat_id, contactId, input.text);
  return bus.board("MessageDeliveryJob").publish({ ...input, contact_id: contactId }, publisher);
}

/** Keep it as history only: it was asked of someone else, so nobody delivers it. */
function record(bus: Bus, chat_id: number, text: string, contact_id?: number): void {
  bus.messages.add(chat_id, contact_id ?? SYSTEM_CONTACT_ID, text);
}

export const messageDelivery = { send, record };
