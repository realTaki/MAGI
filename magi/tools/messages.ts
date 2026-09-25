/**
 * Talking to other chats: search what was said, and queue a message
 * into a chat the MAGI already knows about.
 */

import type { Bus, ExecutableTool } from "../bus/index.js";
import { integerArg, optionalBoundedInteger, stringArg } from "./args.js";

export function messageTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "search_chat_messages", description: "Search active and archived messages in one chat.",
      input_schema: { type: "object", properties: {
        chat_id: { type: "integer" }, query: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 20 },
      }, required: ["chat_id", "query"] },
      async run(args) {
        const chatId = integerArg(args, "chat_id");
        const query = stringArg(args, "query");
        const limit = optionalBoundedInteger(args.limit, 20, "limit", 1, 20);
        return JSON.stringify({ query, chat_id: chatId, messages: bus.messages.searchChat(chatId, query, limit) });
      },
    },
    {
      name: "search_contact_messages", description: "Search one contact's messages across all chats.",
      input_schema: { type: "object", properties: {
        contact_id: { type: "integer", minimum: 1 }, query: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 20 },
      }, required: ["contact_id", "query"] },
      async run(args) {
        const contactId = integerArg(args, "contact_id");
        const query = stringArg(args, "query");
        const limit = optionalBoundedInteger(args.limit, 20, "limit", 1, 20);
        return JSON.stringify({ query, contact_id: contactId, messages: bus.messages.searchContact(contactId, query, limit) });
      },
    },
    {
      // The workspace establishes home once, from the operator's first message, and the
      // only way it moves afterwards is this: the operator asks, the model calls.
      name: "set_home_chat",
      description: "Move the chat this MAGI reports trouble to — the operator's own thread.",
      input_schema: { type: "object", properties: { chat_id: { type: "integer" } }, required: ["chat_id"] },
      async run(args) {
        const chatId = integerArg(args, "chat_id");
        const chat = bus.chats.get(chatId);
        if (!chat) throw new Error(`unknown chat ${chatId}`);
        bus.setHomeChat(chatId);
        return JSON.stringify({ home: chatId, channel: chat.channel, address: chat.delivery_address });
      },
    },
    {
      name: "send_message", description: "Queue a visible message to an existing chat.",
      input_schema: { type: "object", properties: { chat_id: { type: "integer" }, text: { type: "string" } }, required: ["chat_id", "text"] },
      async run(args) {
        const chatId = integerArg(args, "chat_id");
        if (!bus.chats.get(chatId)) throw new Error(`unknown chat ${chatId}`);
        bus.publishDelivery({ chat_id: chatId, text: stringArg(args, "text") }, "tools");
        return `queued to chat ${chatId}`;
      },
    },
  ];
}
