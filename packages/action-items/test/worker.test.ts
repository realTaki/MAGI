import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Bus } from "@magi/bus";
import { ActionItemsWorker } from "../worker.js";

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "magi-action-worker-"));
  const bus = new Bus("@notes.magi", root);
  const worker = new ActionItemsWorker(bus);
  return { bus, worker, async close() { await worker.stop(); bus.close(); rmSync(root, { recursive: true, force: true }); } };
}

test("notes worker independently owns live tools/context and never claims while stopped", async () => {
  const { bus, worker, close } = fixture();
  const board = bus.board("RunToolJob");
  try {
    const id = board.publish({ call: { tool_call_id: "save", name: "save_action_item", arguments: { content: "Finish review" } } }, "test");
    assert.deepEqual(bus.tools.catalog(), []);
    assert.equal(await worker.poll(), false);
    await worker.start();
    assert.deepEqual(bus.tools.catalog().map(tool => tool.name).sort(), ["archive_action_item", "delete_action_item", "list_action_items", "save_action_item"]);
    assert.equal(await worker.poll(), true);
    await worker.stop(); // Drains this accepted call, withdrawing live context.
    assert.equal(board.result(id)?.status, "completed");
    assert.deepEqual(bus.tools.catalog(), []);
    assert.deepEqual(bus.prompts.sections(1), []);
    assert.equal(bus.actionItems.list()[0].content, "Finish review");
    await worker.start();
    assert.match(bus.prompts.sections(1)[0].body, /Finish review/);
    assert.match(bus.prompts.sections(1)[0].body, /version 1/);
    const unrelated = board.publish({ call: { tool_call_id: "other", name: "some_other_tool", arguments: {} } }, "test");
    assert.equal(await worker.poll(), false);
    assert.equal(board.result(unrelated), null);
  } finally { await close(); }
});

test("foreground and subconscious share notes without an agent dependency or new ACL", async () => {
  const { bus, worker, close } = fixture();
  const board = bus.board("RunToolJob");
  try {
    const foreground = bus.chats.forChannel("test", "foreground");
    const subconscious = bus.chats.forChannel("subconscious", "private");
    await worker.start();
    const run = async (chat_id: number, name: string, args: Record<string, unknown>) => {
      const id = board.publish({ chat_id, call: { tool_call_id: name, name, arguments: args } }, "test");
      assert.equal(await worker.poll(), true);
      await worker.stop();
      await worker.start();
      return board.result(id)!;
    };
    assert.equal((await run(foreground.id, "save_action_item", { content: "Shared commitment" })).status, "completed");
    const original = bus.actionItems.list()[0];
    assert.deepEqual(bus.prompts.sections(foreground.id), bus.prompts.sections(subconscious.id));
    const list = await run(subconscious.id, "list_action_items", {});
    assert.equal(list.status, "completed");
    assert.match(JSON.stringify(list), /Shared commitment/);
    assert.equal((await run(subconscious.id, "save_action_item", { action_item_id: original.id, expected_version: original.version, content: "New evidence" })).status, "completed");
    assert.equal((await run(foreground.id, "archive_action_item", { action_item_id: original.id, expected_version: original.version })).status, "failed");
    assert.equal(bus.actionItems.get(original.id)?.content, "New evidence");
    assert.equal((await run(foreground.id, "archive_action_item", { action_item_id: original.id, expected_version: 2 })).status, "completed");
    assert.deepEqual(bus.prompts.sections(subconscious.id), []);
    assert.equal((await run(subconscious.id, "delete_action_item", { action_item_id: original.id, expected_version: 3 })).status, "completed");
    assert.equal((await run(foreground.id, "save_action_item", { action_item_id: original.id, expected_version: 1, content: "Stale resurrection" })).status, "failed");
    assert.deepEqual(bus.actionItems.list(true), []);
  } finally { await close(); }
});

test("shared category tools validate input, preserve old calls and separate idea context", async () => {
  const { bus, worker, close } = fixture();
  const board = bus.board("RunToolJob");
  try {
    const foreground = bus.chats.forChannel("test", "foreground");
    const subconscious = bus.chats.forChannel("subconscious", "private");
    await worker.start();
    const run = async (chat_id: number, name: string, args: Record<string, unknown>) => {
      const id = board.publish({ chat_id, call: { tool_call_id: name, name, arguments: args } }, "test");
      assert.equal(await worker.poll(), true);
      await worker.stop();
      await worker.start();
      return board.result(id)!;
    };
    assert.equal((await run(foreground.id, "save_action_item", { content: "Finish review" })).status, "completed");
    assert.equal((await run(subconscious.id, "save_action_item", { content: "Try a new layout", category: "idea" })).status, "completed");
    const todo = bus.actionItems.list()[0];
    const idea = bus.actionItems.list()[1];
    assert.equal(todo.category, "todo");
    assert.equal(idea.category, "idea");
    const context = bus.prompts.sections(foreground.id)[0].body;
    assert.deepEqual(bus.prompts.sections(foreground.id), bus.prompts.sections(subconscious.id));
    assert.match(context, /Todos:[\s\S]*Finish review[\s\S]*Ideas:[\s\S]*Try a new layout/);
    assert.match(context, /unprioritized possibilities, not commitments/);
    assert.match(context, /not a priority ranking/);
    assert.match(context, /archive completed\/cancelled todos and withdrawn/);
    const ideasOnly = await run(subconscious.id, "list_action_items", { category: "idea" });
    assert.equal(ideasOnly.status, "completed");
    assert.match(JSON.stringify(ideasOnly), /Try a new layout/);
    assert.doesNotMatch(JSON.stringify(ideasOnly), /Finish review/);
    for (const category of ["invalid", "", null, 42, {}]) {
      assert.equal((await run(subconscious.id, "save_action_item", { content: "Invalid", category })).status, "failed");
      assert.equal((await run(subconscious.id, "list_action_items", { category })).status, "failed");
    }
    assert.equal((await run(foreground.id, "save_action_item", { action_item_id: idea.id, expected_version: 1, content: "Try a simpler layout" })).status, "completed");
    assert.equal(bus.actionItems.get(idea.id)?.category, "idea");
    assert.equal((await run(subconscious.id, "save_action_item", { action_item_id: idea.id, expected_version: 1, category: "todo" })).status, "failed");
    assert.equal((await run(subconscious.id, "archive_action_item", { action_item_id: idea.id, expected_version: 2 })).status, "completed");
    assert.equal(bus.actionItems.get(idea.id)?.category, "idea");
    assert.doesNotMatch(bus.prompts.sections(foreground.id)[0].body, /Ideas:|Try a simpler layout/);
    const archived = await run(foreground.id, "list_action_items", { category: "idea", include_archived: true });
    assert.equal(archived.status, "completed");
    assert.match(JSON.stringify(archived), /Try a simpler layout/);
    assert.equal((await run(foreground.id, "save_action_item", { action_item_id: idea.id, expected_version: 3, archived: false })).status, "completed");
    assert.match(bus.prompts.sections(subconscious.id)[0].body, /Ideas:[\s\S]*Try a simpler layout/);
    assert.equal(bus.actionItems.list(true).length, 2);
  } finally { await close(); }
});
