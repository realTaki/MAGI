/**
 * Chat fields the hub needs that a MAGI ChatBook does not carry.
 *
 * The wire id is also `books_chats.delivery_address` with channel `asp`.
 * `next_sequence` is the producer cursor; BUS `last_sequence` is a consumer cursor.
 */

import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const relayChats = sqliteTable("relay_chats", {
  id: text("id").primaryKey(),
  creator: text("creator").notNull(),
  state: text("state", { enum: ["active", "ended"] }).notNull(),
  created_at: integer("created_at").notNull(),
  ended_at: integer("ended_at"),
  description: text("description"),
  kind: text("kind"),
  next_sequence: integer("next_sequence").notNull(),
});
