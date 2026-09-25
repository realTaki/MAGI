/**
 * Talking to other conversations: search what was said, and queue a message
 * into a conversation the MAGI already knows about.
 */

import type { Bus, ExecutableTool } from "../bus/index.js";
import { integerArg, optionalBoundedInteger, stringArg } from "./args.js";

export function messageTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "search_conversation_messages", description: "Search active and archived messages in one conversation.",
      input_schema: { type: "object", properties: {
        conversation_id: { type: "integer" }, query: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 20 },
      }, required: ["conversation_id", "query"] },
      async run(args) {
        const conversationId = integerArg(args, "conversation_id");
        const query = stringArg(args, "query");
        const limit = optionalBoundedInteger(args.limit, 20, "limit", 1, 20);
        return JSON.stringify({ query, conversation_id: conversationId, messages: bus.messages.searchConversation(conversationId, query, limit) });
      },
    },
    {
      name: "search_contact_messages", description: "Search one contact's messages across all conversations.",
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
      name: "set_home_conversation",
      description: "Move the conversation this MAGI reports trouble to — the operator's own thread.",
      input_schema: { type: "object", properties: { conversation_id: { type: "integer" } }, required: ["conversation_id"] },
      async run(args) {
        const conversationId = integerArg(args, "conversation_id");
        const conversation = bus.conversations.get(conversationId);
        if (!conversation) throw new Error(`unknown conversation ${conversationId}`);
        bus.setHomeConversation(conversationId);
        return JSON.stringify({ home: conversationId, channel: conversation.channel, address: conversation.delivery_address });
      },
    },
    {
      name: "send_message", description: "Queue a visible message to an existing conversation.",
      input_schema: { type: "object", properties: { conversation_id: { type: "integer" }, text: { type: "string" } }, required: ["conversation_id", "text"] },
      async run(args) {
        const conversationId = integerArg(args, "conversation_id");
        if (!bus.conversations.get(conversationId)) throw new Error(`unknown conversation ${conversationId}`);
        bus.publishDelivery({ conversation_id: conversationId, text: stringArg(args, "text") }, "tools");
        return `queued to conversation ${conversationId}`;
      },
    },
  ];
}
