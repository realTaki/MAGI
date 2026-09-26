/**
 * The desktop's operator-side cache of ASP data.
 *
 * ASP remains authoritative. These rows preserve received events and queued
 * sends across an unavailable relay or an Electron restart.
 */

import { index, integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const appChats = sqliteTable("chats", {
  id: text("id").primaryKey(),
  record_json: text("record_json", { mode: "json" }).$type<unknown>().notNull(),
});

export const appEvents = sqliteTable("events", {
  chat_id: text("chat_id").notNull(),
  sequence: integer("sequence").notNull(),
  record_json: text("record_json", { mode: "json" }).$type<unknown>().notNull(),
}, (table) => [primaryKey({ columns: [table.chat_id, table.sequence] })]);

export const appAcknowledgements = sqliteTable("acknowledgements", {
  chat_id: text("chat_id").primaryKey(),
  through_sequence: integer("through_sequence").notNull(),
});

export const appOutgoingMessages = sqliteTable("outgoing_messages", {
  id: text("id").primaryKey(),
  chat_id: text("chat_id").notNull(),
  content: text("content").notNull(),
  created_at: integer("created_at").notNull(),
}, (table) => [index("outgoing_messages_order").on(table.created_at, table.id)]);
