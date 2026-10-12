/** Idempotency: the message a sender's key already produced. */

import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspMessageKeys = sqliteTable("asp_message_keys", {
  chat_id: text("chat_id").notNull(),
  sender: text("sender").notNull(),
  key: text("key").notNull(),
  message_id: text("message_id").notNull(),
  sequence: integer("sequence").notNull(),
}, (table) => [primaryKey({ columns: [table.chat_id, table.sender, table.key] })]);
