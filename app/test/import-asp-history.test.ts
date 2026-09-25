import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { openChatStore } from "../main/chat-store.ts";
import { importAspHistory } from "../scripts/import-asp-history.ts";

test("legacy ASP import preserves events without acknowledging them", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-history-import-"));
  t.after(() => rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }));
  const database = path.join(root, "chat.sqlite");
  const fetcher = async (url, options) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/operator") return Response.json({ token: "secret" });
    assert.equal(options.headers.Authorization, "Bearer secret");
    if (endpoint === "/chats") return Response.json({ chats: [{ chat_id: "sess_1", kind: "group" }] });
    if (endpoint === "/chats/sess_1/events") return Response.json({ events: [{ event_id: "evt_1", sequence: 0, type: "chat.message", payload: { content: "hello" } }] });
    throw new Error(`unexpected request: ${endpoint}`);
  };
  assert.deepEqual(await importAspHistory({ database, fetcher }), { chats: 1, events: 1, database });
  const store = await openChatStore(database);
  try {
    assert.equal(store.listChats().length, 1);
    assert.equal(store.listEvents("sess_1").length, 1);
    assert.equal(store.pendingAcks("sess_1").length, 1);
  } finally { store.close(); }
});
