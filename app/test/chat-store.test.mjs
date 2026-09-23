import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { openChatStore } from "../main/chat-store.mjs";

test("desktop chat history and pending receipts survive a restart", {
  skip: Number(process.versions.node.split(".")[0]) < 22,
}, async (t) => {
  const directory = mkdtempSync(path.join(tmpdir(), "magi-chat-"));
  const file = path.join(directory, "app", "chat.sqlite");
  const conversation = { conversation_id: "sess_1", kind: "group", agents: [] };
  const event = {
    event_id: "evt_1", sequence: 3, type: "session.message",
    payload: { sender: "user", content: "hello" },
  };
  const first = await openChatStore(file);
  first.saveConversations([conversation]);
  assert.equal(first.saveEvents("sess_1", [event, event]), 3);
  first.close();

  const reopened = await openChatStore(file);
  // after hooks run in registration order. Close the database before removing
  // its directory; Windows refuses to delete a file that is still open.
  t.after(() => reopened.close());
  t.after(() => rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }));
  assert.deepEqual(reopened.listConversations(), [conversation]);
  assert.deepEqual(reopened.listEvents("sess_1"), [event]);
  assert.deepEqual(reopened.pendingAcks("sess_1"), [event]);
  reopened.markAcknowledged("sess_1", event.sequence);
  assert.deepEqual(reopened.pendingAcks("sess_1"), []);
});
