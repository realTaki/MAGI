import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { Magi } from "../magi.js";
import type { CallLLMJob, LLMMessage } from "../bus/index.js";
import { PiAiClient } from "../providers/client.js";

const workspaces: string[] = [];
afterEach(async () => { for (const path of workspaces.splice(0)) await rm(path, { recursive: true, force: true }); });
async function workspace() { const path = await mkdtemp(join(tmpdir(), "magi-test-")); workspaces.push(path); return path; }

describe("local MAGI agent", () => {
  test("does not open a Python workspace with incompatible Book tables", async () => {
    const path = await workspace();
    await mkdir(join(path, "memories"));
    const db = new Database(join(path, "memories/magi.db"), { create: true });
    db.exec("CREATE TABLE books_settings (id INTEGER PRIMARY KEY, key TEXT NOT NULL, value TEXT NOT NULL)");
    db.close();
    expect(() => new Magi("@alice.magi", { workspace: path })).toThrow("py-magi's SQLite schema");
    const check = new Database(join(path, "memories/magi.db"), { readonly: true });
    expect((check.query("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'books_conversations'").all())).toEqual([]);
    check.close();
  });

  test("migrates Python Books into a separate TypeScript workspace once", async () => {
    const root = await workspace();
    const source = join(root, "python");
    const target = join(root, "typescript");
    await mkdir(join(source, "memories"), { recursive: true });
    const db = new Database(join(source, "memories", "magi.db"), { create: true });
    db.exec(`
      CREATE TABLE books_settings (id INTEGER PRIMARY KEY, key TEXT UNIQUE, value TEXT, created_at TEXT, updated_at TEXT);
      CREATE TABLE books_contacts (id INTEGER PRIMARY KEY, name TEXT, nickname TEXT, role TEXT, last_seen_at TEXT, created_at TEXT, updated_at TEXT);
      CREATE TABLE books_contact_notes (id INTEGER PRIMARY KEY, contact_id INTEGER, note TEXT, kind TEXT, created_at TEXT, updated_at TEXT);
      CREATE TABLE books_conversations (id INTEGER PRIMARY KEY, channel TEXT, delivery_address TEXT, instruction TEXT, topic TEXT, info TEXT, summary TEXT, created_at TEXT, updated_at TEXT);
      CREATE TABLE books_messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, contact_id INTEGER, content TEXT, timestamp TEXT, archived INTEGER, created_at TEXT, updated_at TEXT);
      CREATE TABLE books_memories (id INTEGER PRIMARY KEY, topic TEXT, detail TEXT, kind TEXT, archived INTEGER, created_at TEXT, updated_at TEXT);
      CREATE TABLE books_tasks (id INTEGER PRIMARY KEY, name TEXT, prompt TEXT, source TEXT, enabled INTEGER, cron TEXT, conversation_id INTEGER, created_at TEXT, updated_at TEXT);
      INSERT INTO books_settings VALUES (1, 'provider.name', 'openai', '', '');
      INSERT INTO books_contacts VALUES (0, 'system', NULL, 'system', '2026-01-01', '', '');
      INSERT INTO books_contacts VALUES (1, '@migrate.magi', 'Migrated', 'magi', '2026-01-01', '', '');
      INSERT INTO books_contact_notes VALUES (1, 1, 'kept note', 'permanent', '2026-01-01', '');
      INSERT INTO books_conversations VALUES (1, 'asp', 'session-old', 'instruction', 'topic', 'info', 'summary', '', '');
      INSERT INTO books_messages VALUES (1, 1, 0, 'historic message', '2026-01-01', 0, '', '');
      INSERT INTO books_memories VALUES (1, 'historic memory', 'detail', 'long_term', 0, '2026-01-01', '');
      INSERT INTO books_tasks VALUES (1, 'historic task', 'do work', 'user', 1, '0 9 * * *', 1, '', '');
    `);
    db.close();
    const magi = new Magi("@migrate.magi", { workspace: target, migrationSource: source, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
    try {
      expect(magi.bus.settings.get("provider.name")).toBe("openai");
      expect(magi.bus.settings.get("migration.py_magi")).toBeTruthy();
      expect(magi.bus.contacts.get(1)?.nickname).toBe("Migrated");
      expect(magi.bus.contactNotes.get(1)?.note).toBe("kept note");
      expect(magi.bus.conversations.get(1)).toMatchObject({ topic: "topic", info: "info", summary: "summary" });
      expect(magi.bus.messages.list(1)[0]?.content).toBe("historic message");
      expect(magi.bus.memoryBook.get(1)?.topic).toBe("historic memory");
      expect(magi.bus.tasks.get(1)?.name).toBe("historic task");
    } finally { await magi.stop(); }
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
    magi.start();
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
    magi.start();
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
    magi.start();
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
    magi.start();
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
    magi.start();
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
});
