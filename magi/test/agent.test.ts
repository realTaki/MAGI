import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { Magi } from "../magi.js";
import { SYSTEM_CONTACT_ID, type CallLLMJob, type LLMMessage } from "../bus/index.js";
import { PiAiClient } from "../providers/client.js";

/*
 * Business flow: one MAGI turn (`ARCHITECTURE.md`, "A MAGI process").
 * A `ChatNotify` becomes a `CallLLMJob` and a `RunToolJob`; the answer leaves as a
 * `DeliveryNotify`. Providers and tools answer through the BUS — never by calling
 * each other — and a provider change is verified before it becomes active.
 */

const workspaces: string[] = [];
afterEach(async () => { for (const path of workspaces.splice(0)) await rm(path, { recursive: true, force: true }); });
async function workspace() { const path = await mkdtemp(join(tmpdir(), "magi-test-")); workspaces.push(path); return path; }

describe("local MAGI agent", () => {
  test("pi-ai handles a custom OpenAI-compatible endpoint and native tool calls", async () => {
    const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
    const client = new PiAiClient({
      provider: "custom", model: "local-model", api_key: "key", base_url: "http://localhost:8888/v1",
    }, (async (input, init) => {
      requests.push({ url: String(input), body: JSON.parse(String(init?.body)) as Record<string, unknown> });
      const chunks = requests.length === 1 ? [
        { id: "chatcmpl-1", object: "chat.completion.chunk", created: 1, model: "local-model",
          choices: [{ index: 0, delta: { role: "assistant", content: '{"tool":"plain text"}' }, finish_reason: null }] },
        { id: "chatcmpl-1", object: "chat.completion.chunk", created: 1, model: "local-model",
          choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: "call_1", type: "function",
            function: { name: "read_file", arguments: '{"path":"a.txt"}' } }] }, finish_reason: null }] },
        { id: "chatcmpl-1", object: "chat.completion.chunk", created: 1, model: "local-model",
          choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] },
      ] : [
        { id: "chatcmpl-2", object: "chat.completion.chunk", created: 2, model: "local-model",
          choices: [{ index: 0, delta: { role: "assistant", content: "done" }, finish_reason: null }] },
        { id: "chatcmpl-2", object: "chat.completion.chunk", created: 2, model: "local-model",
          choices: [{ index: 0, delta: {}, finish_reason: "stop" }] },
      ];
      return new Response(chunks.map((chunk) => `data: ${JSON.stringify(chunk)}\n\n`).join("") + "data: [DONE]\n\n",
        { headers: { "Content-Type": "text/event-stream" } });
    }) as typeof fetch);
    const message = await client.complete({ messages: [{ role: "user", content: "hello" }], tools: [
      { name: "read_file", description: "read", input_schema: { type: "object" } },
    ] });
    expect(requests[0].url).toContain("localhost:8888/v1/chat/completions");
    expect((requests[0].body.tools as unknown[])).toHaveLength(1);
    expect(message.content).toBe('{"tool":"plain text"}');
    expect(message.tool_calls).toEqual([{ tool_call_id: "call_1", name: "read_file", arguments: { path: "a.txt" } }]);
    expect(message.provider_state).toBeDefined();
    const followUp = await client.complete({ messages: [
      { role: "user", content: "hello" }, message,
      { role: "tool", tool_call_id: "call_1", tool_name: "read_file", content: "file contents" },
    ], tools: [] });
    expect(followUp.content).toBe("done");
    expect((requests[1].body.messages as Array<{ role: string }>).map((item) => item.role)).toEqual(["user", "assistant", "tool"]);
  });

  test("pi-ai uses its built-in DeepSeek endpoint and model metadata", async () => {
    let endpoint = "";
    let authorization = "";
    const client = new PiAiClient({ provider: "deepseek", model: "deepseek-v4-pro", api_key: "sk-test" },
      (async (input, init) => {
        endpoint = String(input);
        authorization = new Headers(init?.headers).get("authorization") ?? "";
        const chunk = { id: "chatcmpl-1", object: "chat.completion.chunk", created: 1, model: "deepseek-v4-pro",
          choices: [{ index: 0, delta: { role: "assistant", content: "OK" }, finish_reason: "stop" }] };
        return new Response(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`,
          { headers: { "Content-Type": "text/event-stream" } });
      }) as typeof fetch);
    const message = await client.complete({ messages: [{ role: "user", content: "hello" }], tools: [] });
    expect(endpoint).toBe("https://api.deepseek.com/chat/completions");
    expect(authorization).toBe("Bearer sk-test");
    expect(message.content).toBe("OK");
  });

  test("a slow provider does not stall the tools worker", async () => {
    const path = await workspace();
    let entered!: () => void;
    let finish!: (message: LLMMessage) => void;
    const providerEntered = new Promise<void>((resolve) => { entered = resolve; });
    const providerDone = new Promise<LLMMessage>((resolve) => { finish = resolve; });
    const magi = new Magi("@concurrent.magi", {
      workspace: path,
      deliver: () => {},
      tools: [{ name: "echo", description: "echo", input_schema: { type: "object" }, async run(args) { return JSON.stringify(args); } }],
      client: { async complete() { entered(); return providerDone; } },
    });
    await magi.start();
    const chat = magi.chat("wait for the model");
    try {
      await providerEntered;
      const board = magi.bus.board("RunToolJob");
      const id = board.publish({ call: { tool_call_id: "independent", name: "echo", arguments: { ready: true } } }, "test");
      let result = board.result(id);
      for (let attempt = 0; attempt < 100 && !result; attempt++) {
        await Bun.sleep(10);
        result = board.result(id);
      }
      expect(result).toMatchObject({ status: "completed", output: { content: '{"ready":true}' } });
    } finally {
      finish({ role: "assistant", content: "done" });
      await chat;
      await magi.stop();
    }
  });

  test("persists a turn and continues native tool calls through BUS", async () => {
    const path = await workspace();
    await mkdir(join(path, "prompts/agent"), { recursive: true });
    await writeFile(join(path, "prompts/agent/AGENT.md"), "You are Test MAGI.");
    const delivered: string[] = [];
    const requests: CallLLMJob[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      deliver: (text) => { delivered.push(text); },
      client: {
        async complete(job): Promise<LLMMessage> {
          requests.push(job);
          if (requests.length === 1) return {
            role: "assistant", content: "",
            tool_calls: [{ tool_call_id: "call_1", name: "write_file", arguments: { path: "notes/a.txt", content: "saved" } }],
          };
          expect(job.messages.at(-1)).toMatchObject({ role: "tool", tool_call_id: "call_1", content: "wrote 5 bytes" });
          return { role: "assistant", content: "Done." };
        },
      },
    });
    await magi.start();
    const id = await magi.chat("save a note");
    await magi.stop();
    expect(id).toBeGreaterThan(0);
    expect(await readFile(join(path, "notes/a.txt"), "utf8")).toBe("saved");
    expect(requests).toHaveLength(2);
    expect(requests[0].messages[0].content).toContain("You are Test MAGI.");
    const memories = new Database(join(path, "memories/magi.db"), { readonly: true });
    const logs = new Database(join(path, "logs/magi.db"), { readonly: true });
    expect((memories.query("SELECT content FROM books_messages ORDER BY id").all() as Array<{ content: string }>).map((row) => row.content)).toEqual(["save a note", "Done."]);
    expect((logs.query("SELECT type, status FROM jobs ORDER BY id").all() as Array<{ type: string; status: string }>)).toEqual([
      { type: "ChatNotify", status: "completed" },
      { type: "CallLLMJob", status: "completed" },
      { type: "RunToolJob", status: "completed" },
      { type: "CallLLMJob", status: "completed" },
      { type: "DeliveryNotify", status: "completed" },
    ]);
    expect(delivered).toEqual(["Done."]);
    memories.close(); logs.close();
  });

  test("provider errors settle both jobs and produce a reply", async () => {
    const path = await workspace();
    const delivered: string[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      deliver: (text) => { delivered.push(text); },
      client: { async complete() { throw new Error("bad credentials"); } },
    });
    await magi.start();
    const id = await magi.chat("hello");
    expect(magi.bus.board("ChatNotify").result(id)).toMatchObject({ status: "failed", error: "bad credentials" });
    await magi.stop();
    expect(delivered).toEqual(["bad credentials"]);
  });

  test("provider changes are verified before settings become active", async () => {
    const path = await workspace();
    const configured: Array<Record<string, unknown>> = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      client: {
        async complete() { return { role: "assistant", content: "unused" }; },
        async verify(settings) {
          if (settings.api_key === "bad-secret") throw new Error("invalid key bad-secret");
        },
        configure(settings) { configured.push(settings); },
      },
    });
    await magi.start();
    const board = magi.bus.board("ChangeProviderNotify");
    const badId = board.publish({ provider: "openai", model: "gpt-test", api_key: "bad-secret" }, "test");
    let failed = null;
    for (let i = 0; i < 100 && !failed; i++) { failed = board.result(badId); await Bun.sleep(10); }
    expect(failed).toMatchObject({ status: "failed", error: "invalid key [redacted]" });
    expect(magi.bus.settings.get("provider.api_key")).toBeNull();

    const goodId = board.publish({ provider: "custom", model: "gpt-test", api_key: "good-secret", base_url: "https://example.com/v1" }, "test");
    let completed = null;
    for (let i = 0; i < 100 && !completed; i++) { completed = board.result(goodId); await Bun.sleep(10); }
    expect(completed).toMatchObject({ status: "completed" });
    expect(magi.bus.settings.get("provider.name")).toBe("custom");
    expect(magi.bus.settings.get("provider.model")).toBe("gpt-test");
    expect(magi.bus.settings.get("provider.base_url")).toBe("https://example.com/v1");
    expect(magi.bus.settings.get("provider.api_key")).toBe("good-secret");
    expect(configured).toHaveLength(1);
    await magi.stop();
  });

  test("injects active memories and compacts old conversation history", async () => {
    const path = await workspace();
    const requests: CallLLMJob[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      client: {
        async complete(job) {
          requests.push(job);
          if (requests.length === 1) return { role: "assistant", content: "The user is migrating MAGI to TypeScript." };
          return { role: "assistant", content: "Done." };
        },
      },
    });
    magi.bus.memoryBook.save({ topic: "runtime goal", detail: "Finish magi", kind: "long_term" });
    magi.bus.settings.set("provider.context_window", "100");
    const conversation = magi.bus.conversations.forChannel("cli", "terminal");
    for (let i = 0; i < 41; i++) magi.bus.messages.add(conversation.id, 0, `old message ${i}`);
    await magi.start();
    await magi.chat("continue");

    expect(requests).toHaveLength(2);
    expect(requests[0].messages[0].content).toContain("summarising a portion of a chat");
    expect(requests[1].messages[0].content).toContain("Finish magi");
    expect(requests[1].messages[0].content).toContain("The user is migrating MAGI to TypeScript.");
    expect(requests[1].messages[0].content).toContain("codebase_search");
    expect(requests[1].messages[0].content).toContain("Your name: @alice.magi");
    expect(magi.bus.messages.count(conversation.id)).toBe(21);
    await magi.stop();
  });

  test("a turn that answers nothing posts nothing", async () => {
    const path = await workspace();
    const delivered: string[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      deliver: (text) => delivered.push(text),
      client: { async complete() { return { role: "assistant", content: "NO_REPLY" }; } },
    });
    await magi.start();
    await magi.chat("not for you");
    expect(delivered).toEqual([]);
    await magi.stop();
  });

  test("the conversation's members are part of the context", async () => {
    const path = await workspace();
    const requests: CallLLMJob[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      client: { async complete(job) { requests.push(job); return { role: "assistant", content: "ok" }; } },
    });
    const conversation = magi.bus.conversations.forChannel("cli", "terminal");
    const guest = magi.bus.contacts.forAspHandle("@eva-001.magi");
    magi.bus.contacts.update(guest.id, { nickname: "EVA" });
    magi.bus.conversationMembers.add(conversation.id, SYSTEM_CONTACT_ID);
    magi.bus.conversationMembers.add(conversation.id, guest.id);
    magi.bus.conversationMembers.add(conversation.id, guest.id);

    await magi.start();
    await magi.chat("who is here?");
    const system = requests[0].messages[0].content;
    expect(system).toContain("## Members");
    expect(system).toContain(`- id ${guest.id} | @eva-001.magi / EVA | magi`);
    // Being seen twice does not list them twice.
    expect(system.split("\n").filter((line) => line.startsWith(`- id ${guest.id} |`))).toHaveLength(1);
    await magi.stop();
  });
});
