import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
import type { McpConnector } from "../mcp/worker.js";
import { builtinTools } from "../tools/registry.js";

test("MCP worker owns configuration, connections, and dynamic tools", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "ts-magi-mcp-"));
  const closed: string[] = [];
  const connector: McpConnector = async (server) => ({
    tools: [{ name: `${server.name}__echo`, description: "echo", input_schema: { type: "object" }, async run(args) { return JSON.stringify(args); } }],
    async close() { closed.push(server.name); },
  });
  const magi = new Magi("@mcp.magi", {
    workspace, mcpConnector: connector,
    client: { async complete() { return { role: "assistant", content: "unused" }; } },
  });
  const manage = builtinTools(magi.bus).find((tool) => tool.name === "mcp_server")!;
  try {
    magi.start();
    const created = JSON.parse(await manage.run({
      action: "add", name: "demo", connection_type: "stdio", command: "demo-server",
      env: { SECRET: "hidden" },
    })) as { status: string; server: Record<string, unknown> };
    expect(created.status).toBe("created");
    expect(created.server.env).toBeUndefined();
    expect(magi.bus.mcpServers.get("demo")?.env).toEqual({ SECRET: "hidden" });
    expect(magi.tools.catalog().map((tool) => tool.name)).toContain("demo__echo");

    const runs = magi.bus.board("RunToolJob");
    const runId = runs.publish({ call: { tool_call_id: "call-1", name: "demo__echo", arguments: { value: 42 } } }, "test");
    let result = null;
    for (let i = 0; i < 100 && !result; i++) { result = runs.result(runId); await Bun.sleep(10); }
    expect(result).toMatchObject({ status: "completed", output: { content: "{\"value\":42}" } });

    await manage.run({ action: "update", name: "demo", enabled: false });
    expect(closed).toEqual(["demo"]);
    expect(magi.tools.catalog().map((tool) => tool.name)).not.toContain("demo__echo");
    await manage.run({ action: "delete", name: "demo" });
    expect(magi.bus.mcpServers.get("demo")).toBeNull();
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("failed MCP connection does not persist an unusable server", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "ts-magi-mcp-fail-"));
  const magi = new Magi("@mcp-fail.magi", {
    workspace, mcpConnector: async () => { throw new Error("connect refused"); },
    client: { async complete() { return { role: "assistant", content: "unused" }; } },
  });
  const manage = builtinTools(magi.bus).find((tool) => tool.name === "mcp_server")!;
  try {
    magi.start();
    await expect(manage.run({ action: "add", name: "bad", connection_type: "stdio", command: "missing" })).rejects.toThrow("connect refused");
    expect(magi.bus.mcpServers.get("bad")).toBeNull();
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});
