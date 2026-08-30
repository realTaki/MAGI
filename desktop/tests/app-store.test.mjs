import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { openAppStore } from "../core/app-store.mjs";

test("the App store persists settings and per-MAGI conversation metadata", async () => {
  const dataDir = mkdtempSync(join(tmpdir(), "magi-app-store-"));
  let store = await openAppStore({ dataDir });

  try {
    store.setSetting("locale", "zh");
    store.setSetting("sidebar", { collapsed: false });
    assert.equal(store.getSetting("locale"), "zh");
    assert.deepEqual(store.getSetting("sidebar"), { collapsed: false });

    store.saveConversation({
      id: "local-conversation-1",
      magiId: "remote-chief",
      remoteId: "conversation-42",
      title: "Offsite planning",
      syncCursor: "cursor-8",
    });

    const conversations = store.listConversations("remote-chief");
    assert.equal(conversations.length, 1);
    assert.deepEqual(conversations[0], {
      id: "local-conversation-1",
      magiId: "remote-chief",
      remoteId: "conversation-42",
      title: "Offsite planning",
      syncCursor: "cursor-8",
      remoteUpdatedAt: null,
      createdAt: conversations[0].createdAt,
      updatedAt: conversations[0].updatedAt,
    });
    assert.equal(typeof conversations[0].createdAt, "number");
    assert.equal(typeof conversations[0].updatedAt, "number");
    assert.deepEqual(store.listConversations("another-magi"), []);

    store.close();
    store = await openAppStore({ dataDir });
    assert.equal(store.getSetting("locale"), "zh");
    assert.equal(store.listConversations("remote-chief")[0].remoteId, "conversation-42");
  } finally {
    store.close();
    rmSync(dataDir, { recursive: true, force: true });
  }
});
