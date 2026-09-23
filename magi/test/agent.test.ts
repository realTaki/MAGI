import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { Magi } from "../magi.js";
import type { CallLLMJob, LLMMessage } from "../bus/index.js";
import { OpenAICompatibleClient } from "../providers/client.js";

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
      expect(magi.bus.getSetting("provider.name")).toBe("openai");
      expect(magi.bus.getSetting("migration.py_magi")).toBeTruthy();
      expect(magi.bus.contacts.get(1)?.nickname).toBe("Migrated");
      expect(magi.bus.contactNotes.get(1)?.note).toBe("kept note");
      expect(magi.bus.conversations.get(1)).toMatchObject({ topic: "topic", info: "info", summary: "summary" });
      expect(magi.bus.messages.list(1)[0]?.content).toBe("historic message");
      expect(magi.bus.memoryBook.get(1)?.topic).toBe("historic memory");
      expect(magi.bus.tasks.get(1)?.name).toBe("historic task");
    } finally { await magi.stop(); }
  });

  test("provider keeps text as text and reads actions only from native tool_calls", async () => {
    const client = new OpenAICompatibleClient("key", "model", "http://provider.test/v1", async (_url, init) => {
      const request = JSON.parse(String(init?.body)) as { messages: unknown[]; tools: unknown[] };
      expect(request.messages).toEqual([{ role: "user", content: "hello" }]);
      expect(request.tools).toHaveLength(1);
      return Response.json({ choices: [{ message: {
        content: '{"tool":"ignore this plain text"}',
        tool_calls: [{ id: "call_1", type: "function", function: { name: "read_file", arguments: '{"path":"a.txt"}' } }],
      } }] });
    });
    expect(await client.complete({ messages: [{ role: "user", content: "hello" }], tools: [
      { name: "read_file", description: "read", input_schema: { type: "object" } },
    ] })).toEqual({
      role: "assistant", content: '{"tool":"ignore this plain text"}',
      tool_calls: [{ tool_call_id: "call_1", name: "read_file", arguments: { path: "a.txt" } }],
    });
  });

  test("provider translates Anthropic messages and native tool calls", async () => {
    const client = new OpenAICompatibleClient("key", "claude-test", "https://api.anthropic.test/v1", async (url, init) => {
      expect(url).toBe("https://api.anthropic.test/v1/messages");
      expect(new Headers(init?.headers).get("x-api-key")).toBe("key");
      const request = JSON.parse(String(init?.body)) as { system: string; messages: unknown[]; tools: unknown[] };
      expect(request.system).toBe("system prompt");
      expect(request.messages).toEqual([{ role: "user", content: "hello" }]);
      expect(request.tools).toHaveLength(1);
      return Response.json({ content: [
        { type: "thinking", thinking: "inspect", signature: "signed" },
        { type: "text", text: "working" },
        { type: "tool_use", id: "tool-1", name: "read_file", input: { path: "a.txt" } },
      ] });
    }, "claude");
    expect(await client.complete({
      messages: [{ role: "system", content: "system prompt" }, { role: "user", content: "hello" }],
      tools: [{ name: "read_file", description: "read", input_schema: { type: "object" } }],
    })).toEqual({
      role: "assistant", content: "working",
      tool_calls: [{ tool_call_id: "tool-1", name: "read_file", arguments: { path: "a.txt" } }],
      thinking_blocks: [{ type: "thinking", thinking: "inspect", signature: "signed" }],
    });
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
    expect(magi.bus.getSetting("provider.api_key")).toBeNull();

    const goodId = board.publish({ provider: "openai", model: "gpt-test", api_key: "good-secret" }, "test");
    let completed = null;
    for (let i = 0; i < 100 && !completed; i++) { completed = board.result(goodId); await Bun.sleep(10); }
    expect(completed).toMatchObject({ status: "completed" });
    expect(magi.bus.getSetting("provider.name")).toBe("openai");
    expect(magi.bus.getSetting("provider.model")).toBe("gpt-test");
    expect(magi.bus.getSetting("provider.api_key")).toBe("good-secret");
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
    magi.bus.setSetting("provider.context_window", "100");
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
