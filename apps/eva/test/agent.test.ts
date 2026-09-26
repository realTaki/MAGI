import { afterEach, describe, expect, test , sleep} from "./test.js";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { Magi } from "../eva.js";
import { SYSTEM_CONTACT_ID, type CallLLMJob, type LLMMessage } from "@magi/bus";
import { PiAiClient } from "@magi/providers/client.js";
import { AGENT_PROMPT, COMPACTION_PROMPT, SYSTEM_PROMPT } from "@magi/agent/prompt_defaults.js";

/*
 * Business flow: one MAGI turn (`ARCHITECTURE.md`, "A MAGI process").
 * A message delivery job becomes a `CallLLMJob` and a `RunToolJob`; the answer leaves as
 * another one. Providers and tools answer through the BUS — never by calling
 * each other — and a provider change is verified before it becomes active.
 */

const workspaces: string[] = [];
afterEach(async () => { for (const path of workspaces.splice(0)) await rm(path, { recursive: true, force: true }); });
async function workspace() { const path = await mkdtemp(join(tmpdir(), "magi-test-")); workspaces.push(path); return path; }

describe("local MAGI agent", () => {
  test("seeds editable prompts once and keeps system rules outside the workspace", async () => {
    const path = await workspace();
    const magi = new Magi("@templates.magi", { workspace: path });
    expect(await readFile(join(path, "prompts/agent/AGENT.md"), "utf8")).toBe(AGENT_PROMPT);
    expect(await readFile(join(path, "prompts/agent/compaction.md"), "utf8")).toBe(COMPACTION_PROMPT);
    expect(existsSync(join(path, "prompts/agent/defaults"))).toBe(false);
    expect(SYSTEM_PROMPT).toContain("NO_REPLY");
    await magi.stop();
  });

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
    expect(requests[0].body.reasoning_effort).toBe("medium");
    const followUp = await client.complete({ messages: [
      { role: "user", content: "hello" }, message,
      { role: "tool", tool_call_id: "call_1", tool_name: "read_file", content: "file contents" },
    ], tools: [] });
    expect(followUp.content).toBe("done");
    expect((requests[1].body.messages as Array<{ role: string }>).map((item) => item.role)).toEqual(["user", "assistant", "tool"]);
  });

  test("pi-ai exposes selected-model context metadata", () => {
    const client = new PiAiClient({ provider: "custom", model: "local-model", api_key: "key", base_url: "http://localhost:8888/v1" });
    expect(client.contextWindow()).toBe(128_000);
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
        await sleep(10);
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
    // `chat()` completes when the turn has been persisted. Delivery is a
    // separate BUS job, so wait for the channel worker before stopping the
    // supervisor and inspecting its durable result.
    for (let attempt = 0; attempt < 100 && delivered.length === 0; attempt++) await sleep(10);
    await magi.stop();
    expect(id).toBeGreaterThan(0);
    expect(await readFile(join(path, "notes/a.txt"), "utf8")).toBe("saved");
    expect(requests).toHaveLength(2);
    expect(requests[0].messages[0].content).toContain("You are Test MAGI.");
    expect(requests[0].messages[0].content).toContain("Every user-visible reply must be valid Markdown.");
    const memories = new Database(join(path, "memories/books.db"), { readonly: true });
    const jobs = new Database(join(path, "logs/jobs.db"), { readonly: true });
    const stored = (memories.prepare("SELECT content FROM books_messages ORDER BY id").all() as Array<{ content: string }>).map((row) => row.content);
    expect(stored).toHaveLength(2);
    expect(stored[0]).toStartWith(`[contact id ${SYSTEM_CONTACT_ID} | `);
    expect(stored[0]).toContain("]\nsave a note");
    expect(stored[1]).toBe("Done.");
    expect((jobs.prepare("SELECT type, status FROM jobs ORDER BY id").all() as Array<{ type: string; status: string }>)).toEqual([
      { type: "MessageDeliveryJob", status: "completed" },
      { type: "CallLLMJob", status: "completed" },
      { type: "RunToolJob", status: "completed" },
      { type: "CallLLMJob", status: "completed" },
      { type: "MessageDeliveryJob", status: "completed" },
    ]);
    expect(delivered).toEqual(["Done."]);
    memories.close(); jobs.close();
  });

  test("keeps progress visible while an agent completes multiple tool rounds", async () => {
    const path = await workspace();
    const delivered: string[] = [];
    const requests: CallLLMJob[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      deliver: (text) => { delivered.push(text); },
      client: {
        async complete(job): Promise<LLMMessage> {
          requests.push(job);
          if (requests.length === 1) return {
            role: "assistant", content: "正在处理第一步。",
            tool_calls: [{ tool_call_id: "call_1", name: "write_file", arguments: { path: "notes/one.txt", content: "one" } }],
          };
          if (requests.length === 2) return {
            role: "assistant", content: "第一步完成，继续第二步。",
            tool_calls: [{ tool_call_id: "call_2", name: "write_file", arguments: { path: "notes/two.txt", content: "two" } }],
          };
          return { role: "assistant", content: "全部完成。" };
        },
      },
    });
    await magi.start();
    await magi.chat("完成两步工作");
    for (let attempt = 0; attempt < 100 && delivered.length < 3; attempt++) await sleep(10);
    await magi.stop();

    expect(requests).toHaveLength(3);
    expect(requests[2].messages.filter((message) => message.role === "tool").map((message) => ({
      tool_call_id: message.tool_call_id, content: message.content,
    }))).toEqual([
      { tool_call_id: "call_1", content: "wrote 3 bytes" },
      { tool_call_id: "call_2", content: "wrote 3 bytes" },
    ]);
    expect(delivered).toEqual(["正在处理第一步。", "第一步完成，继续第二步。", "全部完成。"]);
  });

  test("the model's own past replies go back without a transcript prefix", async () => {
    const path = await workspace();
    const requests: CallLLMJob[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      deliver: () => {},
      client: { async complete(job) { requests.push(job); return { role: "assistant", content: "noted" }; } },
    });
    await magi.start();
    await magi.chat("first");
    await magi.chat("second");
    const history = requests[1].messages;
    // A reply comes back as it was written. Only what someone else said carries the
    // `[contact id …]` prefix, so the model never learns to write one itself.
    expect(history.find((message) => message.role === "assistant")).toMatchObject({ content: "noted" });
    const asked = history.find((message) => message.role === "user");
    expect(asked?.content).toStartWith(`[contact id ${SYSTEM_CONTACT_ID} | `);
    expect(asked?.content).toContain("\nfirst");
    await magi.stop();
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
    expect(magi.bus.board("MessageDeliveryJob").result(id)).toMatchObject({ status: "failed", error: "bad credentials" });
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
        contextWindow() { return 1_000_000; },
      },
    });
    await magi.start();
    const board = magi.bus.board("ChangeProviderNotify");
    const badId = board.publish({ provider: "openai", model: "gpt-test", api_key: "bad-secret" }, "test");
    let failed = null;
    for (let i = 0; i < 100 && !failed; i++) { failed = board.result(badId); await sleep(10); }
    expect(failed).toMatchObject({ status: "failed", error: "invalid key [redacted]" });
    expect(magi.bus.settings.get("provider.api_key")).toBeNull();

    const goodId = board.publish({ provider: "custom", model: "gpt-test", api_key: "good-secret", base_url: "https://example.com/v1" }, "test");
    let completed = null;
    for (let i = 0; i < 100 && !completed; i++) { completed = board.result(goodId); await sleep(10); }
    expect(completed).toMatchObject({ status: "completed" });
    expect(magi.bus.settings.get("provider.name")).toBe("custom");
    expect(magi.bus.settings.get("provider.model")).toBe("gpt-test");
    expect(magi.bus.settings.get("provider.base_url")).toBe("https://example.com/v1");
    expect(magi.bus.settings.get("provider.api_key")).toBe("good-secret");
    expect(magi.bus.settings.get("provider.context_window")).toBe("1000000");
    expect(configured).toHaveLength(1);
    await magi.stop();
  });

  test("injects active memories and compacts old chat history", async () => {
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
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    for (let i = 0; i < 41; i++) magi.bus.messages.add(chat.id, SYSTEM_CONTACT_ID, `old message ${i}`);
    await magi.start();
    await magi.chat("continue");

    expect(requests).toHaveLength(2);
    expect(requests[0].messages[0].content).toContain("summarising a portion of a chat");
    expect(requests[1].messages[0].content).toContain("Finish magi");
    expect(requests[1].messages[0].content).toContain("The user is migrating MAGI to TypeScript.");
    expect(requests[1].messages[0].content).toContain("codebase_search");
    expect(requests[1].messages[0].content).toContain("Your name: @alice.magi");
    expect(magi.bus.messages.count(chat.id)).toBe(21);
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

  test("the chat's members are part of the context", async () => {
    const path = await workspace();
    const requests: CallLLMJob[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      client: { async complete(job) { requests.push(job); return { role: "assistant", content: "ok" }; } },
    });
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    const guest = magi.bus.contacts.forAspHandle("@eva-001.magi");
    magi.bus.contacts.update(guest.id, { nickname: "EVA" });
    magi.bus.chatMembers.add(chat.id, SYSTEM_CONTACT_ID);
    magi.bus.chatMembers.add(chat.id, guest.id);
    magi.bus.chatMembers.add(chat.id, guest.id);

    await magi.start();
    await magi.chat("who is here?");
    const system = requests[0].messages[0].content;
    expect(system).toContain("## Members");
    expect(system).toContain(`- id ${guest.id} | @eva-001.magi / EVA | magi`);
    // Being seen twice does not list them twice.
    expect(system.split("\n").filter((line) => line.startsWith(`- id ${guest.id} |`))).toHaveLength(1);
    await magi.stop();
  });

  test("the system context is read from the prompt book, not from registries", async () => {
    const path = await workspace();
    const requests: CallLLMJob[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      client: { async complete(job) { requests.push(job); return { role: "assistant", content: "ok" }; } },
    });
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    // Any module can answer a block, and the conversation reaches it: the agent
    // knows nothing about this one.
    magi.bus.prompts.registerSource("probe", "Probe", (chat_id) => `- asked for chat ${chat_id}`);
    await magi.start();
    await magi.chat("hello");
    const system = requests[0].messages[0].content;
    expect(system).toContain(`## Probe\n- asked for chat ${chat.id}`);
    expect(system).toContain("## Available skills");
    expect(system).toContain("- codebase_search:");
    await magi.stop();
  });

  test("prompt blocks keep the order of the workers that registered them", async () => {
    const path = await workspace();
    const magi = new Magi("@alice.magi", {
      workspace: path,
      client: { async complete() { return { role: "assistant", content: "ok" }; } },
    });
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    magi.bus.chatMembers.add(chat.id, SYSTEM_CONTACT_ID);
    magi.bus.memoryBook.save({ topic: "goal", detail: "ship it", kind: "long_term" });
    await magi.start();
    // Identity leads: the order comes from `eva.ts`'s worker order, which is the one
    // place that knows every module. The agent has no opinion about it.
    expect(magi.bus.prompts.sections(chat.id).map((section) => section.title))
      .toEqual(["Identity", "Members", "Available skills", "Long-term memory"]);
    await magi.stop();
  });

  test("a stopped worker's block leaves the system context", async () => {
    const path = await workspace();
    const requests: CallLLMJob[] = [];
    const magi = new Magi("@alice.magi", {
      workspace: path,
      client: { async complete(job) { requests.push(job); return { role: "assistant", content: "ok" }; } },
    });
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    magi.bus.chatMembers.add(chat.id, SYSTEM_CONTACT_ID);
    magi.bus.memoryBook.save({ topic: "goal", detail: "ship it", kind: "long_term" });
    await magi.start();
    await magi.chat("what can you do?");
    const titles = ["Available skills", "Identity", "Members", "Long-term memory"];
    for (const title of titles) expect(requests[0].messages[0].content).toContain(`## ${title}`);

    // Every block belongs to the worker that answers for it: stop one, it is gone.
    for (const worker of ["skills", "contacts", "memory"]) {
      const board = magi.bus.board("ManageWorkerNotify");
      const id = board.publish({ worker, action: "stop" }, "test");
      for (let attempt = 0; attempt < 500 && !board.result(id); attempt++) await sleep(10);
    }
    expect(magi.bus.prompts.sections(chat.id)).toEqual([]);

    await magi.chat("again");
    for (const title of titles) expect(requests[1].messages[0].content).not.toContain(`## ${title}`);
    await magi.stop();
  });
});
