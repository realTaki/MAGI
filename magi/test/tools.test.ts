import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
import { Bus, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID } from "../bus/index.js";
import type { ExecutableTool, LLMMessage } from "../bus/index.js";
import { builtinTools } from "../tools/registry.js";

test("message search includes archived history and send_message uses delivery jobs", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tools-"));
  const delivered: string[] = [];
  const magi = new Magi("@tools.magi", { workspace, deliver: (text) => delivered.push(text), client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  const chat = magi.bus.chats.forChannel("cli", "terminal");
  magi.bus.messages.add(chat.id, SYSTEM_CONTACT_ID, "remember the blue project");
  magi.bus.messages.add(chat.id, MAGI_CONTACT_ID, "assistant answer");
  magi.bus.messages.archiveBefore(chat.id, 1);
  const tools = new Map(builtinTools(magi.bus).map((tool) => [tool.name, tool]));
  try {
    const chatSearch = JSON.parse(await tools.get("search_chat_messages")!.run({ chat_id: chat.id, query: "BLUE" })) as { messages: Array<{ content: string }> };
    expect(chatSearch.messages.map((message) => message.content)).toEqual(["remember the blue project"]);
    const contactSearch = JSON.parse(await tools.get("search_contact_messages")!.run({ contact_id: SYSTEM_CONTACT_ID, query: "project" })) as { messages: Array<{ content: string }> };
    expect(contactSearch.messages).toHaveLength(1);

    await magi.start();
    expect(await tools.get("send_message")!.run({ chat_id: chat.id, text: "progress update" })).toContain("queued");
    for (let i = 0; i < 100 && !delivered.length; i++) await Bun.sleep(10);
    expect(delivered).toEqual(["progress update"]);
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("the home chat moves by tool, not by talking", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tools-home-"));
  const magi = new Magi("@home.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  const tools = new Map(builtinTools(magi.bus).map((tool) => [tool.name, tool]));
  try {
    expect(magi.bus.homeChat()).toBeNull();
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    const moved = JSON.parse(await tools.get("set_home_chat")!.run({ chat_id: chat.id })) as {
      home: number; channel: string; address: string;
    };
    expect(moved).toEqual({ home: chat.id, channel: "cli", address: "terminal" });
    expect(magi.bus.homeChat()).toBe(chat.id);

    // A notice with no chat of its own lands in the new home.
    magi.bus.publishNotice('[manager] worker "tg" could not start');
    expect(magi.bus.messages.list(chat.id).at(-1)?.content).toContain("could not start");

    await expect(tools.get("set_home_chat")!.run({ chat_id: 999 })).rejects.toThrow("unknown chat");
    expect(magi.bus.homeChat()).toBe(chat.id);
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("a tool the catalog does not have is answered without a job", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tools-unknown-"));
  const seen: LLMMessage[][] = [];
  const magi = new Magi("@unknown.magi", {
    workspace, deliver: () => {},
    client: {
      async complete(job) {
        seen.push(job.messages);
        return seen.length === 1
          ? { role: "assistant", content: "", tool_calls: [{ tool_call_id: "call-1", name: "nope", arguments: {} }] }
          : { role: "assistant", content: "recovered" };
      },
    },
  });
  try {
    await magi.start();
    await magi.chat("try a tool that does not exist");
    expect(seen[1].filter((message) => message.role === "tool")).toEqual([
      { role: "tool", tool_call_id: "call-1", tool_name: "nope", content: "unknown tool nope", is_error: true },
    ]);
    expect(magi.bus.board("RunToolJob").claim("probe")).toBeNull();
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("the catalog asks live sources, and a name clash fails at registration", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tools-catalog-"));
  const bus = new Bus("@catalog.magi", workspace);
  const tool = (name: string): ExecutableTool => ({ name, description: "probe", input_schema: { type: "object" }, async run() { return "ok"; } });
  try {
    let provided: ExecutableTool[] = [];
    bus.tools.registerSource("probe", () => provided);
    expect(bus.tools.catalog()).toEqual([]);

    // The source changed its mind without telling anyone: the catalog follows it anyway.
    provided = [tool("probe__one")];
    expect(bus.tools.catalog().map((entry) => entry.name)).toEqual(["probe__one"]);
    expect(bus.tools.get("probe__one")).not.toBeNull();
    provided = [];
    expect(bus.tools.get("probe__one")).toBeNull();

    provided = [tool("probe__one")];
    expect(() => bus.tools.registerSource("clash", () => provided)).toThrow("already registered by probe");
    expect(bus.tools.catalog().map((entry) => entry.name)).toEqual(["probe__one"]);
  } finally {
    bus.close();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("a tool call left over from a restart is failed instead of re-run", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tools-restart-"));
  const before = new Bus("@restart.magi", workspace);
  const chat = before.chats.forChannel("cli", "terminal");
  const id = before.board("RunToolJob").publish({
    call: { tool_call_id: "call-1", name: "send_message", arguments: { chat_id: chat.id, text: "again" } },
  }, "test");
  before.close();

  const delivered: string[] = [];
  const magi = new Magi("@restart.magi", {
    workspace, deliver: (text) => delivered.push(text),
    client: { async complete() { return { role: "assistant", content: "unused" }; } },
  });
  try {
    await magi.start();
    for (let i = 0; i < 20; i++) await Bun.sleep(10);
    expect(magi.bus.board("RunToolJob").result(id)).toMatchObject({ status: "failed" });
    expect(delivered).toEqual([]);
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});
