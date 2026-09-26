import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import Database from "better-sqlite3";

import { openChatStore } from "../main/chat-store.ts";

/*
 * Business flow: sending a message, desktop side (`ARCHITECTURE.md`, "ASP").
 * The transcript is written before the acknowledgment goes out, and receipts
 * that are still pending survive a restart.
 */

test("desktop chat history and pending receipts survive a restart", {
  skip: Number(process.versions.node.split(".")[0]) < 22,
}, async (t) => {
  const directory = mkdtempSync(path.join(tmpdir(), "magi-chat-"));
  const file = path.join(directory, "app", "chat.sqlite");
  const chat = { chat_id: "sess_1", kind: "group", agents: [] };
  const event = {
    event_id: "evt_1", sequence: 3, type: "chat.message",
    payload: { sender: "@user.magi", content: "hello" },
  };
  const first = await openChatStore(file);
  first.saveChats([chat]);
  assert.equal(first.saveEvents("sess_1", [event, event]), 3);
  first.queueOutgoingMessage({ id: "out_1", chatId: "sess_1", content: "send later", createdAt: 4 });
  first.close();

  const reopened = await openChatStore(file);
  // after hooks run in registration order. Close the database before removing
  // its directory; Windows refuses to delete a file that is still open.
  t.after(() => reopened.close());
  t.after(() => rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }));
  assert.deepEqual(reopened.listChats(), [chat]);
  assert.deepEqual(reopened.listEvents("sess_1"), [event]);
  assert.deepEqual(reopened.pendingAcks("sess_1"), [event]);
  assert.deepEqual(reopened.listOutgoingMessages(), [
    { id: "out_1", chatId: "sess_1", content: "send later", createdAt: 4 },
  ]);
  reopened.removeOutgoingMessage("out_1");
  assert.deepEqual(reopened.listOutgoingMessages(), []);
  reopened.markAcknowledged("sess_1", event.sequence);
  assert.deepEqual(reopened.pendingAcks("sess_1"), []);
});

test("the versioned baseline adopts an existing desktop cache without replacing it", (t) => {
  const directory = mkdtempSync(path.join(tmpdir(), "magi-chat-legacy-"));
  const file = path.join(directory, "chat.sqlite");
  const legacy = new Database(file);
  const chat = { chat_id: "sess_existing", kind: "bot", agents: ["@eva-000.magi"] };
  legacy.exec(`
    CREATE TABLE chats (id TEXT PRIMARY KEY, record_json TEXT NOT NULL);
    CREATE TABLE events (chat_id TEXT NOT NULL, sequence INTEGER NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY (chat_id, sequence));
    CREATE TABLE acknowledgements (chat_id TEXT PRIMARY KEY, through_sequence INTEGER NOT NULL);
    CREATE TABLE outgoing_messages (id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, content TEXT NOT NULL, created_at INTEGER NOT NULL);
  `);
  legacy.prepare("INSERT INTO chats VALUES (?, ?)").run(chat.chat_id, JSON.stringify(chat));
  legacy.close();

  const store = openChatStore(file);
  t.after(() => store.close());
  assert.deepEqual(store.listChats(), [chat]);

  const migrations = new Database(file, { readonly: true });
  t.after(() => migrations.close());
  t.after(() => rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }));
  assert.equal(migrations.prepare("SELECT count(*) AS count FROM __drizzle_migrations").get().count, 1);
  assert.equal(
    migrations.prepare("SELECT count(*) AS count FROM sqlite_master WHERE type = 'index' AND name = 'outgoing_messages_order'").get().count,
    1,
  );
});
