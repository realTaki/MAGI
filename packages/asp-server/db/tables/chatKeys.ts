/** Idempotency: the conversation a creator's key already produced. */

import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspChatKeys = sqliteTable("asp_chat_keys", {
  creator: text("creator").notNull(),
  key: text("key").notNull(),
  chat_id: text("chat_id").notNull(),
  sequence: integer("sequence"),
}, (table) => [primaryKey({ columns: [table.creator, table.key] })]);
