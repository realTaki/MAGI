/** Who is in a conversation, with the status they hold there. */

import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspParticipants = sqliteTable("asp_participants", {
  chat_id: text("chat_id").notNull(),
  handle: text("handle").notNull(),
  status: text("status", { enum: ["invited", "joined", "left"] }).notNull(),
  joined_at: integer("joined_at"),
  left_at: integer("left_at"),
}, (table) => [primaryKey({ columns: [table.chat_id, table.handle] })]);
