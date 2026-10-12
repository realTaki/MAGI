/** Known ASP peers and each contact's inbound policy. */

import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspContacts = sqliteTable("asp_contacts", {
  handle: text("handle").primaryKey(),
  token: text("token").notNull().unique(),
  name: text("name"),
  nickname: text("nickname"),
  inbound_policy: text("inbound_policy", { enum: ["allowlist", "open"] }).notNull(),
  managed: integer("managed", { mode: "boolean" }).notNull(),
});

/** Contacts permitted to initiate chats with an allowlist-protected contact. */
export const aspContactAllowlist = sqliteTable("asp_contact_allowlist", {
  contact_handle: text("contact_handle").notNull()
    .references(() => aspContacts.handle, { onDelete: "cascade", onUpdate: "cascade" }),
  allowed_handle: text("allowed_handle").notNull(),
}, (table) => [primaryKey({ columns: [table.contact_handle, table.allowed_handle] })]);
