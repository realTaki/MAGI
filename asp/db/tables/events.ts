/**
 * The relay events themselves, ordered per conversation.
 *
 * `payload_json` is the event body; `type` says which one it is
 * (`chat.message`, `chat.invited`, …). A message event is deleted once every
 * intended recipient has acknowledged it — `messageRecipients` holds those
 * receipts, `eventAcks` every answer.
 */

import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspEvents = sqliteTable("asp_events", {
  chat_id: text("chat_id").notNull(),
  sequence: integer("sequence").notNull(),
  event_id: text("event_id").notNull().unique(),
  type: text("type").notNull(),
  created_at: integer("created_at").notNull(),
  payload_json: text("payload_json", { mode: "json" }).$type<Record<string, unknown>>().notNull(),
}, (table) => [primaryKey({ columns: [table.chat_id, table.sequence] })]);
