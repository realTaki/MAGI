import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
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
