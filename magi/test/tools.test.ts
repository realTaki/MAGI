import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
import type { LLMMessage } from "../bus/index.js";
import { builtinTools } from "../tools/registry.js";

test("message search includes archived history and send_message uses delivery jobs", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-tools-"));
  const delivered: string[] = [];
  const magi = new Magi("@tools.magi", { workspace, deliver: (text) => delivered.push(text), client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  const conversation = magi.bus.conversations.forChannel("cli", "terminal");
  magi.bus.messages.add(conversation.id, 0, "remember the blue project");
  magi.bus.messages.add(conversation.id, 1, "assistant answer");
  magi.bus.messages.archiveBefore(conversation.id, 1);
  const tools = new Map(builtinTools(magi.bus).map((tool) => [tool.name, tool]));
  try {
    const conversationSearch = JSON.parse(await tools.get("search_conversation_messages")!.run({ conversation_id: conversation.id, query: "BLUE" })) as { messages: Array<{ content: string }> };
    expect(conversationSearch.messages.map((message) => message.content)).toEqual(["remember the blue project"]);
    const contactSearch = JSON.parse(await tools.get("search_contact_messages")!.run({ contact_id: 0, query: "project" })) as { messages: Array<{ content: string }> };
    expect(contactSearch.messages).toHaveLength(1);

    magi.start();
    expect(await tools.get("send_message")!.run({ conversation_id: conversation.id, text: "progress update" })).toContain("queued");
    for (let i = 0; i < 100 && !delivered.length; i++) await Bun.sleep(10);
    expect(delivered).toEqual(["progress update"]);
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
    magi.start();
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
