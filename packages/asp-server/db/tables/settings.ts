/**
 * Relay settings: the operator identity, and nothing that belongs to a chat.
 *
 * `value_json` is JSON in a text column: drizzle encodes and decodes it, and the
 * shape is validated where it is read (`operator.ts`).
 */

import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspSettings = sqliteTable("asp_settings", {
  key: text("key").primaryKey(),
  value_json: text("value_json", { mode: "json" }).$type<unknown>().notNull(),
  updated_at: integer("updated_at").notNull(),
});
