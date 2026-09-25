/**
 * Message events wait for each intended recipient: `acked` is that receipt.
 *
 * Whoever was in the conversation when the message was said gets a row, and the
 * event goes away when none of them is left unacked.
 */

import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

import { aspEvents } from "./events.ts";

export const aspMessageRecipients = sqliteTable("asp_message_recipients", {
  event_id: text("event_id").notNull().references(() => aspEvents.event_id, { onDelete: "cascade" }),
  handle: text("handle").notNull(),
  acked: integer("acked", { mode: "boolean" }).notNull().default(false),
}, (table) => [primaryKey({ columns: [table.event_id, table.handle] })]);
