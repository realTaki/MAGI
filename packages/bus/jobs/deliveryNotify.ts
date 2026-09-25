/**
 * "Send this text back out on the chat's channel."
 *
 * How an outgoing message is handled is owned here: the reply is recorded as this
 * MAGI's own row — the book is what the workspace keeps — and the payload the channel
 * posts is the same text it was recorded with, so neither end has to piece it
 * together from somewhere else.
 */

import type { Bus } from "../bus.js";
import { MAGI_CONTACT_ID } from "../books/contactBook.js";

export type DeliveryNotify = {
  chat_id: number;
  text: string;
  channel?: string;
  address?: string;
};

/** Record the reply, then queue it for the chat's channel. Returns the job id. */
function send(bus: Bus, input: DeliveryNotify, publisher = "agent"): number {
  const chat = bus.chats.get(input.chat_id);
  if (!chat) throw new Error(`chat ${input.chat_id} does not exist`);
  bus.messages.add(input.chat_id, MAGI_CONTACT_ID, input.text);
  return bus.board("DeliveryNotify").publish({ ...input, channel: chat.channel, address: chat.delivery_address }, publisher);
}

export const deliveryNotify = { send };
