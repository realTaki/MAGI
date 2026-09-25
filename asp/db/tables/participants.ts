/** Who is in a conversation, with the status they hold there. */

import { primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspParticipants = sqliteTable("asp_participants", {
  chat_id: text("chat_id").notNull(),
  handle: text("handle").notNull(),
  record_json: text("record_json", { mode: "json" }).$type<unknown>().notNull(),
}, (table) => [primaryKey({ columns: [table.chat_id, table.handle] })]);
