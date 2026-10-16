/** Relay credentials and inbound policy. BUS contacts have no token. */

import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const relayPrincipals = sqliteTable("relay_principals", {
  handle: text("handle").primaryKey(),
  token: text("token").notNull().unique(),
  inbound_policy: text("inbound_policy", { enum: ["allowlist", "open"] }).notNull(),
  managed: integer("managed", { mode: "boolean" }).notNull(),
  public_key: text("public_key"),
});

export const relayAllowlist = sqliteTable("relay_allowlist", {
  contact_handle: text("contact_handle").notNull(),
  allowed_handle: text("allowed_handle").notNull(),
}, (table) => [primaryKey({ columns: [table.contact_handle, table.allowed_handle] })]);
