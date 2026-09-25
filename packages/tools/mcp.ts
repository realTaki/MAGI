/**
 * MCP server configuration.
 *
 * The tool does not connect anything itself: it publishes a
 * `ChangeMcpServerNotify` job and waits for the mcp worker (which owns the
 * connections) to report back. `env`/`headers` never leave the worker, so the
 * model only ever sees the public shape of a server.
 */

import type { Bus, ChangeMcpServerNotify, ExecutableTool, McpConnectionType, McpServerConfig } from "@magi/bus";
import { setTimeout as sleep } from "node:timers/promises";
import { stringArg } from "./args.js";

function mcpServerFromArgs(name: string, args: Record<string, unknown>, current: McpServerConfig | null): McpServerConfig {
  const connectionType = (args.connection_type ?? current?.connection_type) as McpConnectionType | undefined;
  if (connectionType !== "stdio" && connectionType !== "sse" && connectionType !== "streamable_http") throw new Error("connection_type must be stdio, sse, or streamable_http");
  const command = typeof args.command === "string" ? args.command.trim() : current?.command ?? null;
  const url = typeof args.url === "string" ? args.url.trim() : current?.url ?? null;
  if (connectionType === "stdio" && !command) throw new Error("stdio servers require command");
  if (connectionType !== "stdio" && !url) throw new Error(`${connectionType} servers require url`);
  return { name, connection_type: connectionType, command, url,
    args: args.args === undefined ? current?.args ?? [] : stringArray(args.args, "args"),
    env: args.env === undefined ? current?.env ?? {} : stringRecord(args.env, "env"),
    headers: args.headers === undefined ? current?.headers ?? {} : stringRecord(args.headers, "headers"),
    enabled: typeof args.enabled === "boolean" ? args.enabled : current?.enabled ?? true,
    connect_timeout: optionalPositiveNumber(args.connect_timeout, current?.connect_timeout ?? undefined) ?? null,
    execute_timeout: optionalPositiveNumber(args.execute_timeout, current?.execute_timeout ?? undefined) ?? null };
}

function stringArray(value: unknown, key: string): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) throw new Error(`${key} must be an array of strings`);
  return value;
}

function stringRecord(value: unknown, key: string): Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value) || !Object.values(value).every((item) => typeof item === "string")) throw new Error(`${key} must map strings to strings`);
  return value as Record<string, string>;
}

function optionalPositiveNumber(value: unknown, fallback?: number): number | undefined {
  if (value === undefined) return fallback;
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) throw new Error("timeouts must be positive numbers");
  return value;
}

function publicMcpServer(server: McpServerConfig): Omit<McpServerConfig, "env" | "headers"> {
  const { env: _env, headers: _headers, ...visible } = server;
  return visible;
}

async function publishMcpChange(bus: Bus, input: ChangeMcpServerNotify): Promise<void> {
  const board = bus.board("ChangeMcpServerNotify");
  const id = board.publish(input, "tools");
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const result = board.result(id);
    if (result?.status === "completed") return;
    if (result?.status === "failed") throw new Error(result.error ?? "MCP configuration failed");
    await sleep(10);
  }
  throw new Error("MCP worker did not apply the change within 30 seconds");
}

export function mcpTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "mcp_server", description: "List, add, update, or delete MCP servers. Confirm with the operator before changing configuration.",
      input_schema: { type: "object", properties: {
        action: { type: "string", enum: ["list", "add", "update", "delete"] }, name: { type: "string" },
        connection_type: { type: "string", enum: ["stdio", "sse", "streamable_http"] }, command: { type: "string" },
        args: { type: "array", items: { type: "string" } }, url: { type: "string" }, enabled: { type: "boolean" },
        env: { type: "object" }, headers: { type: "object" }, connect_timeout: { type: "number" },
        execute_timeout: { type: "number" },
      }, required: ["action"] },
      async run(args) {
        const action = stringArg(args, "action");
        if (action === "list") return JSON.stringify({ servers: bus.mcpServers.list().map(publicMcpServer) });
        if (action !== "add" && action !== "update" && action !== "delete") throw new Error("action must be list, add, update, or delete");
        const name = stringArg(args, "name");
        // No double underscore: it is what joins a server name to its tool names, so
        // keeping it out of server names makes every MCP tool name unambiguous.
        if (!/^(?!.*__)[a-zA-Z0-9_.-]{1,64}$/.test(name)) throw new Error("MCP server name must be ASCII without spaces, at most 64 characters, and without a double underscore");
        const current = bus.mcpServers.get(name);
        if (action === "delete") {
          if (!current) return JSON.stringify({ status: "not_found", name });
          await publishMcpChange(bus, { action, name });
          return JSON.stringify({ status: "deleted", name });
        }
        if (action === "add" && current) throw new Error(`server ${name} already exists`);
        if (action === "update" && !current) throw new Error(`server ${name} does not exist`);
        const server = mcpServerFromArgs(name, args, current);
        await publishMcpChange(bus, { action, name, server });
        return JSON.stringify({ status: action === "add" ? "created" : "updated", server: publicMcpServer(server) });
      },
    },
  ];
}
