/**
 * "Someone said something" — the job that opens one agent turn.
 *
 * How a received message is handled is owned here, with the job that carries it: the
 * row is the durable fact — the turn that answers reads it back from the book, never
 * from the payload — and the job is only the wake-up call. A message that was
 * addressed to someone else is recorded the same way, just without a turn.
 */

import type { Bus } from "../bus.js";
import { SYSTEM_CONTACT_ID } from "../books/contactBook.js";

export type ChatNotify = {
  text: string;
  contact_id?: number;
  chat_id?: number;
  channel?: string;
  delivery_address?: string;
};

/** Record what was said, then open its turn. Returns the `ChatNotify` job id. */
function receive(bus: Bus, input: ChatNotify, publisher = "channel"): number {
  let chatId = input.chat_id;
  if (!chatId) {
    if (!input.channel?.trim() || !input.delivery_address?.trim()) throw new Error("ChatNotify needs chat_id or channel and delivery_address");
    chatId = bus.chats.forChannel(input.channel.trim(), input.delivery_address.trim()).id;
  }
  if (chatId === undefined) throw new Error("chat_id is missing");
  if (!bus.chats.get(chatId)) throw new Error(`chat ${chatId} does not exist`);
  record(bus, chatId, input.text, input.contact_id);
  return bus.board("ChatNotify").publish({ ...input, chat_id: chatId }, publisher);
}

/** Record it without opening a turn. An unnamed speaker is the operator. */
function record(bus: Bus, chat_id: number, text: string, contact_id?: number): void {
  bus.messages.add(chat_id, contact_id ?? SYSTEM_CONTACT_ID, text);
}

export const chatNotify = { receive, record };
