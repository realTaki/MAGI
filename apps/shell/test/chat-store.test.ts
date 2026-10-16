import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { Bus } from "@magi/bus";

import { openChatStore } from "@magi/webapp/chat-store.js";
import { createLocalApi } from "../shell.ts";

/*
 * Business flow: sending a message, desktop side (`docs/architecture.md`, "Relay").
 * The transcript is written before the acknowledgment goes out, and receipts
 * that are still pending survive a restart.
 */

test("desktop chat history and pending receipts survive a restart", {
  skip: Number(process.versions.node.split(".")[0]) < 22,
}, async (t) => {
  const directory = mkdtempSync(path.join(tmpdir(), "magi-chat-"));
  const file = path.join(directory, "shell", "chat.sqlite");
  const chat = { chat_id: "sess_1", kind: "group", agents: [] };
  const event = {
    event_id: "evt_1", sequence: 3, type: "chat.message",
    payload: { sender: "@user.magi", content: "hello" },
  };
  let bus = new Bus("@user.magi", path.dirname(file));
  let activeStore;
  t.after(() => { activeStore?.close(); bus.close(); rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  const first = activeStore = await openChatStore(file, bus);
  first.saveChats([chat]);
  assert.equal(first.saveEvents("sess_1", [event, event]), 3);
  first.queueOutgoingMessage({ id: "out_1", chatId: "sess_1", content: "send later", createdAt: 4 });
  first.close();
  activeStore = null;
  bus.close();
  bus = new Bus("@user.magi", path.dirname(file));
  const reopened = activeStore = await openChatStore(file, bus);
  // after hooks run in registration order. Close the database before removing
  // its directory; Windows refuses to delete a file that is still open.
  assert.deepEqual(reopened.listChats(), [{ ...chat, participants: [{ handle: "@user.magi", status: "joined" }], name: undefined, topic: undefined, description: undefined, created_at: undefined }]);
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

test("disposing the backend after opening chat storage does not throw", async (t) => {
  const directory = mkdtempSync(path.join(tmpdir(), "magi-chat-dispose-"));
  const api = createLocalApi({
    paths: {
      home: directory,
      userData: path.join(directory, "electron"),
      checkout: path.join(directory, "checkout"),
    },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {}, openExternal: async () => {}, copy: () => {},
  });
  t.after(async () => { await api.dispose(); rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  await api["chat.saveChats"]([]);
  await api.dispose();
  await assert.rejects(api.ready(), /disposed/);
  await assert.rejects(api["chat.listChats"](), /disposed/);
});
