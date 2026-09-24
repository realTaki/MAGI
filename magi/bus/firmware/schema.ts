import { sql } from "drizzle-orm";
import { index, integer, real, sqliteTable, text, unique } from "drizzle-orm/sqlite-core";

/** The Book tables of one MAGI workspace (`<workspace>/memories/magi.db`). */

export const conversations = sqliteTable("books_conversations", {
  id: integer("id").primaryKey(),
  channel: text("channel").notNull(),
  delivery_address: text("delivery_address").notNull(),
  instruction: text("instruction").notNull().default(""),
  topic: text("topic").notNull().default(""),
  info: text("info").notNull().default(""),
  summary: text("summary").notNull().default(""),
}, (table) => [unique("books_conversations_channel_address").on(table.channel, table.delivery_address)]);

export const messages = sqliteTable("books_messages", {
  id: integer("id").primaryKey(),
  conversation_id: integer("conversation_id").notNull(),
  contact_id: integer("contact_id").notNull(),
  content: text("content").notNull(),
  created_at: text("created_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
  archived: integer("archived", { mode: "boolean" }).notNull().default(false),
}, (table) => [index("books_messages_conversation").on(table.conversation_id, table.id)]);

export const settings = sqliteTable("books_settings", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
});

export const memories = sqliteTable("books_memories", {
  id: integer("id").primaryKey(),
  topic: text("topic").notNull(),
  detail: text("detail").notNull(),
  kind: text("kind").$type<"temporary" | "short_term" | "long_term">().notNull().default("temporary"),
  archived: integer("archived", { mode: "boolean" }).notNull().default(false),
  created_at: text("created_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
});

export const tasks = sqliteTable("books_tasks", {
  id: integer("id").primaryKey(),
  name: text("name").notNull().unique(),
  prompt: text("prompt").notNull(),
  source: text("source").$type<"user" | "proactive">().notNull().default("user"),
  enabled: integer("enabled", { mode: "boolean" }).notNull().default(true),
  cron: text("cron").notNull(),
  conversation_id: integer("conversation_id").notNull(),
  last_fired_minute: text("last_fired_minute"),
});

export const contacts = sqliteTable("books_contacts", {
  id: integer("id").primaryKey(),
  name: text("name").notNull().unique(),
  nickname: text("nickname"),
  role: text("role").$type<"system" | "authorized" | "stranger" | "magi" | "third_party_agent">().notNull().default("stranger"),
  last_seen_at: text("last_seen_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
});

export const contactNotes = sqliteTable("books_contact_notes", {
  id: integer("id").primaryKey(),
  contact_id: integer("contact_id").notNull().references(() => contacts.id, { onDelete: "cascade" }),
  note: text("note").notNull(),
  kind: text("kind").$type<"permanent" | "daily">().notNull().default("permanent"),
  created_at: text("created_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
});

export const mcpServers = sqliteTable("books_mcp_servers", {
  name: text("name").primaryKey(),
  connection_type: text("connection_type").$type<"stdio" | "sse" | "streamable_http">().notNull(),
  command: text("command"),
  args: text("args", { mode: "json" }).$type<string[]>().notNull().default([]),
  url: text("url"),
  env: text("env", { mode: "json" }).$type<Record<string, string>>().notNull().default({}),
  headers: text("headers", { mode: "json" }).$type<Record<string, string>>().notNull().default({}),
  enabled: integer("enabled", { mode: "boolean" }).notNull().default(true),
  connect_timeout: real("connect_timeout"),
  execute_timeout: real("execute_timeout"),
  sse_read_timeout: real("sse_read_timeout"),
});
