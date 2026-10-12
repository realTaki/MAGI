/** Every recipient that answered, whether or not the event carried a message. */

import { primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

import { aspEvents } from "./events.ts";

export const aspEventAcks = sqliteTable("asp_event_acks", {
  event_id: text("event_id").notNull().references(() => aspEvents.event_id, { onDelete: "cascade" }),
  handle: text("handle").notNull(),
}, (table) => [primaryKey({ columns: [table.event_id, table.handle] })]);
