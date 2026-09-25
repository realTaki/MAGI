import { eq } from "drizzle-orm";
import { integer, real, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";

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
});

export type McpServerConfig = typeof mcpServers.$inferSelect;
export type McpConnectionType = McpServerConfig["connection_type"];

export class McpServerBook {
  constructor(private readonly db: BusDb) {}

  list(): McpServerConfig[] {
    return this.db.select().from(mcpServers).orderBy(mcpServers.name).all();
  }
  get(name: string): McpServerConfig | null {
    return this.db.select().from(mcpServers).where(eq(mcpServers.name, name)).get() ?? null;
  }
  save(config: McpServerConfig): void {
    const { name: _name, ...fields } = config;
    this.db.insert(mcpServers).values(config)
      .onConflictDoUpdate({ target: mcpServers.name, set: fields })
      .run();
  }
  delete(name: string): boolean {
    // Ask for the deleted row so this works consistently across SQLite drivers.
    return this.db.delete(mcpServers).where(eq(mcpServers.name, name)).returning({ name: mcpServers.name }).get() !== undefined;
  }
}
