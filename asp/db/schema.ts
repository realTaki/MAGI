/**
 * The ASP relay's tables, declared once and in one place: the relay keeps one
 * store for all of them (unlike a MAGI's Books, which each own a table), so this
 * file is where a field is changed and `npm run db:generate` picks it up.
 *
 * `record_json` / `payload_json` columns are JSON in a text column: drizzle
 * encodes and decodes them, the shapes are validated where they are read.
 */

import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

/** Relay settings: the operator identity, and nothing that belongs to a chat. */
export const aspSettings = sqliteTable("asp_settings", {
  key: text("key").primaryKey(),
  value_json: text("value_json", { mode: "json" }).$type<unknown>().notNull(),
  updated_at: integer("updated_at").notNull(),
});

/** Registered agents: one row per handle, the record holds token and policy. */
export const aspAgents = sqliteTable("asp_agents", {
  handle: text("handle").primaryKey(),
  record_json: text("record_json", { mode: "json" }).$type<unknown>().notNull(),
});

/** Conversations. `next_sequence` is the sequence the next event will get. */
export const aspChats = sqliteTable("asp_chats", {
  id: text("id").primaryKey(),
  record_json: text("record_json", { mode: "json" }).$type<unknown>().notNull(),
  next_sequence: integer("next_sequence").notNull(),
});

/** Who is in a conversation, with the status they hold there. */
export const aspParticipants = sqliteTable("asp_participants", {
  chat_id: text("chat_id").notNull(),
  handle: text("handle").notNull(),
  record_json: text("record_json", { mode: "json" }).$type<unknown>().notNull(),
}, (table) => [primaryKey({ columns: [table.chat_id, table.handle] })]);

/** The relay events themselves, ordered per conversation. */
export const aspEvents = sqliteTable("asp_events", {
  chat_id: text("chat_id").notNull(),
  sequence: integer("sequence").notNull(),
  event_id: text("event_id").notNull().unique(),
  type: text("type").notNull(),
  created_at: integer("created_at").notNull(),
  payload_json: text("payload_json", { mode: "json" }).$type<Record<string, unknown>>().notNull(),
}, (table) => [primaryKey({ columns: [table.chat_id, table.sequence] })]);

/** Message events wait for each intended recipient: `acked` is that receipt. */
export const aspMessageRecipients = sqliteTable("asp_message_recipients", {
  event_id: text("event_id").notNull().references(() => aspEvents.event_id, { onDelete: "cascade" }),
  handle: text("handle").notNull(),
  acked: integer("acked", { mode: "boolean" }).notNull().default(false),
}, (table) => [primaryKey({ columns: [table.event_id, table.handle] })]);

/** Every recipient that answered, whether or not the event carried a message. */
export const aspEventAcks = sqliteTable("asp_event_acks", {
  event_id: text("event_id").notNull().references(() => aspEvents.event_id, { onDelete: "cascade" }),
  handle: text("handle").notNull(),
}, (table) => [primaryKey({ columns: [table.event_id, table.handle] })]);

/** Idempotency: the message a sender's key already produced. */
export const aspMessageKeys = sqliteTable("asp_message_keys", {
  chat_id: text("chat_id").notNull(),
  sender: text("sender").notNull(),
  key: text("key").notNull(),
  message_id: text("message_id").notNull(),
  sequence: integer("sequence").notNull(),
}, (table) => [primaryKey({ columns: [table.chat_id, table.sender, table.key] })]);

/** Idempotency: the conversation a creator's key already produced. */
export const aspChatKeys = sqliteTable("asp_chat_keys", {
  creator: text("creator").notNull(),
  key: text("key").notNull(),
  chat_id: text("chat_id").notNull(),
  sequence: integer("sequence"),
}, (table) => [primaryKey({ columns: [table.creator, table.key] })]);
