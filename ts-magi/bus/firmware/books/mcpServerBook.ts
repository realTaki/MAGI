import type { Database } from "bun:sqlite";

export type McpConnectionType = "stdio" | "sse" | "streamable_http";
export type McpServerConfig = {
  name: string; connection_type: McpConnectionType; command?: string; args?: string[]; url?: string;
  env?: Record<string, string>; headers?: Record<string, string>; enabled?: boolean;
  connect_timeout?: number; execute_timeout?: number; sse_read_timeout?: number;
};
type McpServerRow = { name: string; connection_type: McpConnectionType; command: string | null; args: string; url: string | null; env: string; headers: string; enabled: number; connect_timeout: number | null; execute_timeout: number | null; sse_read_timeout: number | null };

export class McpServerBook {
  constructor(private readonly db: Database) {}

  list(): McpServerConfig[] { return (this.db.prepare("SELECT * FROM books_mcp_servers ORDER BY name").all() as McpServerRow[]).map(fromRow); }
  get(name: string): McpServerConfig | null {
    const row = this.db.prepare("SELECT * FROM books_mcp_servers WHERE name = ?").get(name) as McpServerRow | undefined;
    return row ? fromRow(row) : null;
  }
  save(config: McpServerConfig): void {
    this.db.prepare(`INSERT INTO books_mcp_servers
      (name, connection_type, command, args, url, env, headers, enabled, connect_timeout, execute_timeout, sse_read_timeout)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET
      connection_type=excluded.connection_type, command=excluded.command, args=excluded.args, url=excluded.url,
      env=excluded.env, headers=excluded.headers, enabled=excluded.enabled, connect_timeout=excluded.connect_timeout,
      execute_timeout=excluded.execute_timeout, sse_read_timeout=excluded.sse_read_timeout`)
      .run(config.name, config.connection_type, config.command ?? null, JSON.stringify(config.args ?? []), config.url ?? null,
        JSON.stringify(config.env ?? {}), JSON.stringify(config.headers ?? {}), config.enabled === false ? 0 : 1,
        config.connect_timeout ?? null, config.execute_timeout ?? null, config.sse_read_timeout ?? null);
  }
  delete(name: string): boolean { return this.db.prepare("DELETE FROM books_mcp_servers WHERE name = ?").run(name).changes === 1; }
}

function fromRow(row: McpServerRow): McpServerConfig {
  return { name: row.name, connection_type: row.connection_type, command: row.command ?? undefined,
    args: JSON.parse(row.args) as string[], url: row.url ?? undefined, env: JSON.parse(row.env) as Record<string, string>,
    headers: JSON.parse(row.headers) as Record<string, string>, enabled: !!row.enabled,
    connect_timeout: row.connect_timeout ?? undefined, execute_timeout: row.execute_timeout ?? undefined,
    sse_read_timeout: row.sse_read_timeout ?? undefined };
}
