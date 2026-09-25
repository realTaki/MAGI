import type { McpServerConfig } from "../books/mcpServerBook.js";

/** Add, update or delete one MCP server; the mcp worker owns the connection. */

export type ChangeMcpServerNotify = {
  action: "add" | "update" | "delete";
  name: string;
  server?: McpServerConfig;
};
