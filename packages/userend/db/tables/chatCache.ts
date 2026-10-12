/**
 * What Books cannot hold: the full ASP event JSON the UI still paints, and the
 * outbound queue while ASP is down. Chat directory, members, and the ack
 * cursor live on the operator BUS.
 */

import { index, integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const appEvents = sqliteTable("events", {
  chat_id: text("chat_id").notNull(),
  sequence: integer("sequence").notNull(),
  record_json: text("record_json", { mode: "json" }).$type<unknown>().notNull(),
}, (table) => [primaryKey({ columns: [table.chat_id, table.sequence] })]);

export const appOutgoingMessages = sqliteTable("outgoing_messages", {
  id: text("id").primaryKey(),
  chat_id: text("chat_id").notNull(),
  content: text("content").notNull(),
  created_at: integer("created_at").notNull(),
}, (table) => [index("outgoing_messages_order").on(table.created_at, table.id)]);
