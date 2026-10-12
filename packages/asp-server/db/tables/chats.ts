/**
 * Conversations.
 *
 * `next_sequence` is the sequence the next event in this conversation will get;
 * it is the relay's own counter, kept beside the record for ordering.
 */

import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspChats = sqliteTable("asp_chats", {
  id: text("id").primaryKey(),
  creator: text("creator").notNull(),
  state: text("state", { enum: ["active", "ended"] }).notNull(),
  topic: text("topic"),
  created_at: integer("created_at").notNull(),
  ended_at: integer("ended_at"),
  description: text("description"),
  kind: text("kind"),
  next_sequence: integer("next_sequence").notNull(),
});
