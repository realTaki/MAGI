import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
import type { McpConnector } from "../mcp/worker.js";
import { builtinTools } from "../tools/registry.js";

test("MCP worker owns configuration, connections, and dynamic tools", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-mcp-"));
  const closed: string[] = [];
  const modelCatalogs: string[][] = [];
  const connector: McpConnector = async (server) => ({
    tools: [{ name: `${server.name}__echo`, description: "echo", input_schema: { type: "object" }, async run(args) { return JSON.stringify(args); } }],
    async close() { closed.push(server.name); },
  });
  const magi = new Magi("@mcp.magi", {
    workspace, mcpConnector: connector, deliver: () => {},
    client: { async complete(job) { modelCatalogs.push(job.tools.map((tool) => tool.name)); return { role: "assistant", content: "done" }; } },
  });
  const manage = builtinTools(magi.bus).find((tool) => tool.name === "mcp_server")!;
  try {
    await magi.start();
    const created = JSON.parse(await manage.run({
      action: "add", name: "demo", connection_type: "stdio", command: "demo-server",
      env: { SECRET: "hidden" },
    })) as { status: string; server: Record<string, unknown> };
    expect(created.status).toBe("created");
    expect(created.server.env).toBeUndefined();
    expect(magi.bus.mcpServers.get("demo")?.env).toEqual({ SECRET: "hidden" });
    expect(magi.bus.tools.catalog().map((tool) => tool.name)).toContain("demo__echo");
    await magi.chat("use the available tools");
    expect(modelCatalogs.at(-1)).toContain("demo__echo");

    const runs = magi.bus.board("RunToolJob");
    const runId = runs.publish({ call: { tool_call_id: "call-1", name: "demo__echo", arguments: { value: 42 } } }, "test");
    let result = null;
    for (let i = 0; i < 100 && !result; i++) { result = runs.result(runId); await Bun.sleep(10); }
    expect(result).toMatchObject({ status: "completed", output: { content: "{\"value\":42}" } });

    await manage.run({ action: "update", name: "demo", enabled: false });
    expect(closed).toEqual(["demo"]);
    expect(magi.bus.tools.catalog().map((tool) => tool.name)).not.toContain("demo__echo");
    await manage.run({ action: "delete", name: "demo" });
    expect(magi.bus.mcpServers.get("demo")).toBeNull();
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("an MCP server that cannot connect at boot reaches the operator", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-mcp-notice-"));
  const delivered: string[] = [];
  const magi = new Magi("@mcp-notice.magi", {
    workspace, mcpConnector: async () => { throw new Error("connect refused"); },
    deliver: (text) => delivered.push(text),
    client: { async complete() { return { role: "assistant", content: "unused" }; } },
  });
  try {
    magi.bus.setHomeChat(magi.bus.chats.forChannel("cli", "terminal").id);
    magi.bus.mcpServers.save({
      name: "dead", connection_type: "stdio", command: "missing", args: [], url: null,
      env: {}, headers: {}, enabled: true, connect_timeout: null, execute_timeout: null,
    });
    await magi.start();
    for (let i = 0; i < 200 && delivered.length === 0; i++) await Bun.sleep(10);
    expect(delivered[0]).toContain("[mcp] dead");
    expect(delivered[0]).toContain("connect refused");
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("failed MCP connection does not persist an unusable server", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-mcp-fail-"));
  const magi = new Magi("@mcp-fail.magi", {
    workspace, mcpConnector: async () => { throw new Error("connect refused"); },
    client: { async complete() { return { role: "assistant", content: "unused" }; } },
  });
  const manage = builtinTools(magi.bus).find((tool) => tool.name === "mcp_server")!;
  try {
    await magi.start();
    await expect(manage.run({ action: "add", name: "bad", connection_type: "stdio", command: "missing" })).rejects.toThrow("connect refused");
    expect(magi.bus.mcpServers.get("bad")).toBeNull();
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});
