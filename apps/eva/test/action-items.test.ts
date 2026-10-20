import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Bus, SYSTEM_CONTACT_ID, type LLMRequest } from "@magi/bus";
import { actionItemTools } from "@magi/action-items/tools.js";
import { ActionItemsWorker } from "@magi/action-items/worker.js";
import { Magi } from "./harness.js";

test("working notes persist across restart, archive explicitly, and remain EVA-local", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-actions-"));
  let bus = new Bus("@notes.magi", join(path, "one"));
  const other = new Bus("@other.magi", join(path, "two"));
  try {
    const first = bus.actionItems.save({ content: "  Finish the review, waiting for results  " });
    assert.equal(first.content, "Finish the review, waiting for results");
    assert.ok(first.created_at && first.updated_at);
    const second = bus.actionItems.save({ content: "Check the deployment" });
    assert.deepEqual(bus.actionItems.list().map((item) => item.id), [first.id, second.id]);
    assert.deepEqual(other.actionItems.list(), []);
    assert.throws(() => bus.actionItems.save({ content: " " }), /content is required/);
    assert.throws(() => bus.actionItems.save({ id: first.id, content: " " }), /content is required/);
    assert.throws(() => bus.actionItems.save({ id: 999, content: "missing" }), /not found/);
    bus.actionItems.save({ id: first.id, content: "Review ready; await user decision" });
    bus.actionItems.save({ id: second.id, archived: true });
    bus.close();
    bus = new Bus("@notes.magi", join(path, "one"));
    assert.deepEqual(bus.actionItems.list().map((item) => item.content), ["Review ready; await user decision"]);
    assert.equal(bus.actionItems.list(true).length, 2);
    assert.equal(bus.actionItems.get(second.id)?.archived, true);
    bus.actionItems.save({ id: second.id, archived: false });
    assert.equal(bus.actionItems.list().length, 2);
    assert.equal(bus.actionItems.delete(first.id), true);
    assert.equal(bus.actionItems.delete(first.id), false);
    assert.equal(bus.actionItems.get(first.id), null);
  } finally {
    bus.close(); other.close(); await rm(path, { recursive: true, force: true });
  }
});

test("action item tools validate CRUD and retire context with their worker", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-action-tools-"));
  const bus = new Bus("@notes.magi", path);
  const worker = new ActionItemsWorker(bus);
  try {
    const tools = new Map(actionItemTools(bus).map((tool) => [tool.name, tool]));
    const run = (name: string, args: Record<string, unknown>) => tools.get(name)!.run(args);
    await assert.rejects(run("save_action_item", { action_item_id: "1", content: "x" }), /positive integer/);
    await assert.rejects(run("save_action_item", { content: 123 }), /string/);
    await assert.rejects(run("save_action_item", { archived: "false" }), /boolean/);
    await assert.rejects(run("list_action_items", { include_archived: "true" }), /boolean/);
    const { action_item: item } = JSON.parse(await run("save_action_item", { content: "Remember current blocker" }));
    assert.deepEqual(bus.tools.catalog(), []);
    await worker.start();
    assert.ok(bus.tools.catalog().some((tool) => tool.name === "save_action_item"));
    assert.match(bus.prompts.sections(1).find((section) => section.title === "Action items")!.body, /Remember current blocker/);
    const updated = JSON.parse(await run("save_action_item", { action_item_id: item.id, expected_version: item.version, content: "Blocker resolved" }));
    assert.equal(updated.action_item.content, "Blocker resolved");
    await run("archive_action_item", { action_item_id: item.id, expected_version: updated.action_item.version });
    assert.deepEqual(JSON.parse(await run("list_action_items", {})).action_items, []);
    assert.equal(JSON.parse(await run("list_action_items", { include_archived: true })).action_items.length, 1);
    assert.equal(bus.prompts.sections(1).some((section) => section.title === "Action items"), false);
    await run("save_action_item", { action_item_id: item.id, expected_version: bus.actionItems.get(item.id)!.version, archived: false });
    await worker.stop();
    assert.deepEqual(bus.tools.catalog(), []);
    assert.deepEqual(bus.prompts.sections(1), []);
    await run("delete_action_item", { action_item_id: item.id, expected_version: bus.actionItems.get(item.id)!.version });
    await assert.rejects(run("delete_action_item", { action_item_id: item.id, expected_version: 1 }), /not found/);
  } finally { await worker.stop(); bus.close(); await rm(path, { recursive: true, force: true }); }
});

test("compaction preserves working notes, tool results update this turn, next turn refreshes context", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-action-context-"));
  const requests: LLMRequest[] = [];
  let interaction = 0;
  let id = 0;
  const magi = new Magi("@notes.magi", { workspace: path, client: {
    async complete(job) {
      requests.push(structuredClone(job));
      if (!job.tools.length) return { role: "assistant", content: "Old chat summary" };
      interaction++;
      if (interaction === 1) return { role: "assistant", content: "", tool_calls: [
        { tool_call_id: "update-note", name: "save_action_item", arguments: { action_item_id: id, expected_version: 1, content: "Review finished; waiting for approval" } },
      ] };
      if (interaction === 2) {
        const result = job.messages.find((message) => message.role === "tool");
        assert.match(result?.content ?? "", /Review finished; waiting for approval/);
        return { role: "assistant", content: "NO_REPLY" };
      }
      return { role: "assistant", content: "NO_REPLY" };
    },
  } });
  try {
    id = magi.bus.actionItems.save({ content: "Review still underway" }).id;
    const chat = magi.bus.chats.forChannel("test-owner", "terminal");
    for (let i = 0; i < 41; i++) magi.bus.messages.add(chat.id, SYSTEM_CONTACT_ID, `old message ${i}`);
    magi.bus.settings.set("provider.context_window", "100");
    await magi.start();
    await magi.chat("continue");
    const turn = requests.filter((request) => request.tools.length);
    assert.equal(turn.length, 2);
    assert.match(turn[0].messages[0].content, /Review still underway/);
    assert.match(turn[0].messages[0].content, /Old chat summary/);
    assert.equal(turn[0].messages[0].content, turn[1].messages[0].content);
    await magi.chat("what remains?");
    assert.match(requests.at(-1)!.messages[0].content, /Review finished; waiting for approval/);
    assert.doesNotMatch(requests.at(-1)!.messages[0].content, /Review still underway/);
    magi.bus.actionItems.save({ id, archived: true });
    await magi.chat("done");
    assert.doesNotMatch(requests.at(-1)!.messages[0].content, /Review finished; waiting for approval/);
    assert.match(requests.at(-1)!.messages[0].content, /Onboarding:/);
  } finally { await magi.stop(); await rm(path, { recursive: true, force: true }); }
});


test("preset notes seed atomically once and stay retired after deletion and restart", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-action-seed-"));
  let bus = new Bus("@notes.magi", path);
  try {
    assert.throws(() => bus.actionItems.seedOnce("", "note"), /seed key/);
    assert.throws(() => bus.actionItems.seedOnce("onboarding", " "), /content is required/);
    assert.equal(bus.settings.get("action_items.seed.onboarding"), null);
    const item = bus.actionItems.seedOnce("onboarding", "Ask about preferences")!;
    assert.ok(item);
    bus.actionItems.save({ id: item.id, content: "Preferences learned", archived: true });
    assert.equal(bus.actionItems.seedOnce("onboarding", "new default"), null);
    assert.equal(bus.actionItems.get(item.id)?.content, "Preferences learned");
    assert.equal(bus.actionItems.get(item.id)?.archived, true);
    bus.actionItems.delete(item.id);
    bus.close();
    bus = new Bus("@notes.magi", path);
    assert.equal(bus.actionItems.seedOnce("onboarding", "Ask again"), null);
    assert.deepEqual(bus.actionItems.list(true), []);
    assert.ok(bus.actionItems.seedOnce("another-preset", "Another note"));
  } finally { bus.close(); await rm(path, { recursive: true, force: true }); }
});

test("foreground and background snapshots cannot overwrite, archive, or delete newer notes", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-action-cas-"));
  const bus = new Bus("@notes.magi", path);
  const tools = new Map(actionItemTools(bus).map((tool) => [tool.name, tool]));
  const run = (name: string, args: Record<string, unknown>) => tools.get(name)!.run(args);
  try {
    const item = bus.actionItems.save({ content: "Original shared note" });
    const foreground = bus.actionItems.get(item.id)!;
    const background = bus.actionItems.list()[0];
    assert.equal(foreground.version, 1);
    await run("save_action_item", { action_item_id: foreground.id, expected_version: foreground.version, content: "Fresh foreground progress" });
    for (const [name, extra] of [
      ["save_action_item", { content: "Stale background progress" }],
      ["archive_action_item", {}],
      ["delete_action_item", {}],
    ] as const) {
      await assert.rejects(run(name, { action_item_id: background.id, expected_version: background.version, ...extra }), /changed; read its current version/);
      await assert.rejects(run(name, { action_item_id: background.id, ...extra }), /expected_version/);
    }
    assert.equal(bus.actionItems.get(item.id)?.content, "Fresh foreground progress");
    assert.equal(bus.actionItems.get(item.id)?.archived, false);
    assert.equal(bus.actionItems.get(item.id)?.version, 2);
    const latest = JSON.parse(await run("list_action_items", {})).action_items[0];
    const archived = JSON.parse(await run("archive_action_item", { action_item_id: latest.id, expected_version: latest.version })).action_item;
    assert.equal(archived.version, 3);
    await assert.rejects(run("delete_action_item", { action_item_id: item.id, expected_version: 2 }), /changed/);
    await run("delete_action_item", { action_item_id: item.id, expected_version: archived.version });
    const replacement = bus.actionItems.save({ content: "Unrelated new note" });
    assert.ok(replacement.id > item.id);
    await assert.rejects(run("save_action_item", { action_item_id: item.id, expected_version: 1, content: "Old snapshot" }), /not found/);
    assert.equal(bus.actionItems.get(replacement.id)?.content, "Unrelated new note");
    assert.throws(() => bus.actionItems.save({ id: replacement.id, content: "invalid version", expectedVersion: 0 }), /positive integer/);
  } finally { bus.close(); await rm(path, { recursive: true, force: true }); }
});


test("version migration preserves existing notes and starts revision at one", () => {
  const db = new DatabaseSync(":memory:");
  try {
    const migration = (name: string) => readFileSync(new URL(`../../../../packages/bus/drizzle/books/${name}`, import.meta.url), "utf8");
    db.exec(migration("0014_action_items.sql"));
    db.exec("INSERT INTO books_action_items (id, content, archived, created_at, updated_at) VALUES (42, 'Saved note', 1, 'created', 'updated')");
    db.exec(migration("0016_action_item_versions.sql"));
    assert.deepEqual({ ...db.prepare("SELECT * FROM books_action_items WHERE id = 42").get() }, {
      id: 42, content: "Saved note", archived: 1, created_at: "created", updated_at: "updated", version: 1,
    });
    db.exec("DELETE FROM books_action_items; INSERT INTO books_action_items (content) VALUES ('New note')");
    assert.equal(db.prepare("SELECT id FROM books_action_items").get()?.id, 43);
  } finally { db.close(); }
});
