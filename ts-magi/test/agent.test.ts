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
async function workspace() { const path = await mkdtemp(join(tmpdir(), "ts-magi-test-")); workspaces.push(path); return path; }

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
    magi.bus.memoryBook.save({ topic: "runtime goal", detail: "Finish ts-magi", kind: "long_term" });
    const conversation = magi.bus.conversations.forChannel("cli", "terminal");
    for (let i = 0; i < 41; i++) magi.bus.messages.add(conversation.id, 0, `old message ${i}`);
    magi.start();
    await magi.chat("continue");

    expect(requests).toHaveLength(2);
    expect(requests[0].messages[0].content).toContain("Summarize durable facts");
    expect(requests[1].messages[0].content).toContain("Finish ts-magi");
    expect(requests[1].messages[0].content).toContain("The user is migrating MAGI to TypeScript.");
    expect(magi.bus.messages.count(conversation.id)).toBe(11);
    await magi.stop();
  });
});
