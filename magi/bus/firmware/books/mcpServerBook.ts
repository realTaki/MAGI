import { eq } from "drizzle-orm";
import type { BooksDb } from "../database.js";
import { mcpServers } from "../schema.js";

export type McpServerConfig = typeof mcpServers.$inferSelect;
export type McpConnectionType = McpServerConfig["connection_type"];

export class McpServerBook {
  constructor(private readonly db: BooksDb) {}

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
    // bun:sqlite reports no row count, so ask for the deleted row back instead.
    return this.db.delete(mcpServers).where(eq(mcpServers.name, name)).returning({ name: mcpServers.name }).get() !== undefined;
  }
}
