/**
 * Conversations.
 *
 * `next_sequence` is the sequence the next event in this conversation will get;
 * it is the relay's own counter, kept beside the record for ordering.
 */

import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspChats = sqliteTable("asp_chats", {
  id: text("id").primaryKey(),
  record_json: text("record_json", { mode: "json" }).$type<unknown>().notNull(),
  next_sequence: integer("next_sequence").notNull(),
});
