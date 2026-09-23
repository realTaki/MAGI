import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { Magi } from "../magi.js";
import type { CallLLMJob, LLMMessage } from "../bus/index.js";

const workspaces: string[] = [];
afterEach(async () => { for (const path of workspaces.splice(0)) await rm(path, { recursive: true, force: true }); });
async function workspace() { const path = await mkdtemp(join(tmpdir(), "ts-magi-test-")); workspaces.push(path); return path; }

describe("local MAGI agent", () => {
  test("persists a turn and continues native tool calls through BUS", async () => {
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
});
