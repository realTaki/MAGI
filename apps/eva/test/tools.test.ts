import { expect, test , sleep} from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../eva.js";
import { Bus, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID } from "@magi/bus";
import type { ExecutableTool, LLMMessage } from "@magi/bus";
import { builtinTools, ToolsWorker } from "@magi/built-in-tools/worker.js";

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
    expect(chatSearch.messages).toHaveLength(1);
    expect(chatSearch.messages[0]!.content).toBe("remember the blue project");
    const contactSearch = JSON.parse(await tools.get("search_contact_messages")!.run({ contact_id: SYSTEM_CONTACT_ID, query: "project" })) as { messages: Array<{ content: string }> };
    expect(contactSearch.messages).toHaveLength(1);

    await magi.start();
    expect(await tools.get("send_message")!.run({ chat_id: chat.id, text: "progress update" })).toContain("queued");
    for (let i = 0; i < 100 && !delivered.length; i++) await sleep(10);
    expect(delivered).toEqual(["progress update"]);
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("request_replace uses the job chat, not arguments.chat_id", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-replace-chat-id-"));
  const bus = new Bus("@replace-chat.magi", workspace);
  const worker = new ToolsWorker(bus);
  const from = bus.chats.forChannel("cli", "from");
  const spoof = bus.chats.forChannel("cli", "spoof");
  const ownerMessage = bus.messages.add(from.id, SYSTEM_CONTACT_ID, "Replace this EVA from this conversation");
  try {
    const id = bus.board("RunToolJob").publish({
      call: { tool_call_id: "call-replace", name: "request_replace", arguments: { chat_id: spoof.id } },
      chat_id: from.id, message_id: ownerMessage,
    }, "test");
    expect(await worker.poll()).toBeTrue();
    let result = bus.board("RunToolJob").result(id);
    for (let i = 0; i < 100 && !result; i++) {
      await sleep(10);
      result = bus.board("RunToolJob").result(id);
    }
    expect(result).toMatchObject({ status: "completed" });
    const replace = bus.board("ReplaceSelfJob").claim("test");
    expect(replace?.input.chat_id).toBe(from.id);
  } finally {
    await worker.stop();
    bus.close();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("send_message to another chat is not rewritten to the requester chat", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tools-chat-id-"));
  const bus = new Bus("@tools-chat.magi", workspace);
  const worker = new ToolsWorker(bus);
  const from = bus.chats.forChannel("cli", "from");
  const to = bus.chats.forChannel("cli", "to");
  const ownerMessage = bus.messages.add(from.id, SYSTEM_CONTACT_ID, "Send hello other chat to the selected conversation");
  try {
    const id = bus.board("RunToolJob").publish({
      call: { tool_call_id: "call-other", name: "send_message", arguments: { chat_id: to.id, text: "hello other chat" } },
      chat_id: from.id, message_id: ownerMessage,
    }, "test");
    expect(await worker.poll()).toBeTrue();
    let result = bus.board("RunToolJob").result(id);
    for (let i = 0; i < 100 && !result; i++) {
      await sleep(10);
      result = bus.board("RunToolJob").result(id);
    }
    expect(result).toMatchObject({ status: "completed" });
    expect(bus.messages.list(to.id).map((row) => row.content)).toEqual(["hello other chat"]);
    expect(bus.messages.list(from.id).map((row) => row.id)).toEqual([ownerMessage]);
  } finally {
    await worker.stop();
    bus.close();
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
  const ownerMessage = before.messages.add(chat.id, SYSTEM_CONTACT_ID, "Send again in this conversation");
  const id = before.board("RunToolJob").publish({
    chat_id: chat.id, message_id: ownerMessage,
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
    for (let i = 0; i < 20; i++) await sleep(10);
    expect(magi.bus.board("RunToolJob").result(id)).toMatchObject({ status: "failed" });
    expect(delivered).toEqual([]);
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});
