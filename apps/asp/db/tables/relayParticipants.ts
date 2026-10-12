/** Invite / join / leave. BUS membership is a set of joined contacts. */

import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const relayParticipants = sqliteTable("relay_participants", {
  chat_id: text("chat_id").notNull(),
  handle: text("handle").notNull(),
  status: text("status", { enum: ["invited", "joined", "left"] }).notNull(),
  joined_at: integer("joined_at"),
  left_at: integer("left_at"),
}, (table) => [primaryKey({ columns: [table.chat_id, table.handle] })]);
