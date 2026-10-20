import assert from "node:assert/strict";
import { afterEach, test, sleep } from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { existsSync, readFileSync, mkdirSync, copyFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { Bus, messageDelivery, type LLMRequest } from "@magi/bus";
import { MemoryBook } from "@magi/bus/books/memoryBook.js";
import { workspaceDatabase } from "@magi/bus/drizzle/database.js";
import { migrate } from "@magi/bus/sqlite.js";
import { MemoryWorker } from "@magi/memory/worker.js";
import { memoryTools } from "@magi/memory/tools.js";
import { subconsciousTool } from "@magi/subconscious/tools.js";
import { SubconsciousWorker } from "@magi/subconscious/worker.js";
import { ProvidersWorker } from "@magi/providers/worker.js";
import { Magi } from "./harness.js";

const paths: string[] = [];
afterEach(async () => { for (const path of paths.splice(0)) await rm(path, { recursive: true, force: true }); });
async function workspace() { const path = await mkdtemp(join(tmpdir(), "magi-memory-")); paths.push(path); return path; }
async function until(check: () => boolean) { for (let i = 0; i < 300 && !check(); i++) await sleep(10); assert.ok(check(), "condition did not settle"); }

test("memory migration preserves existing active and archived facts", () => {
  const db = new DatabaseSync(":memory:");
  try {
    db.exec(`CREATE TABLE books_memories (
      id INTEGER PRIMARY KEY, topic TEXT NOT NULL, detail TEXT NOT NULL,
      kind TEXT NOT NULL DEFAULT 'temporary', archived INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
      INSERT INTO books_memories(id, topic, detail, kind, archived, created_at)
      VALUES(7, 'Preference', 'Source: user message 4; concise replies', 'long_term', 0, '2026-01-01'),
            (9, 'Old plan', 'Source: user message 2; cancelled', 'temporary', 1, '2026-01-02');`);
    const source = new URL("../../../packages/bus/drizzle/books/0020_memory_versions.sql", import.meta.url);
    const compiled = new URL("../../../../packages/bus/drizzle/books/0020_memory_versions.sql", import.meta.url);
    db.exec(readFileSync(existsSync(source) ? source : compiled, "utf8"));
    const book = new MemoryBook(workspaceDatabase(db));
    assert.deepEqual(book.list(true).map(row => [row.id, row.detail, row.archived, row.version, row.created_at]), [
      [7, "Source: user message 4; concise replies", false, 1, "2026-01-01"],
      [9, "Source: user message 2; cancelled", true, 1, "2026-01-02"],
    ]);
    book.delete(9);
    const next = book.save({ topic: "New fact", detail: "A different fact" });
    assert.ok(next.id > 9, "migration retains a non-recycling ID sequence");
    assert.throws(() => book.save({ id: 9, expectedVersion: 1, detail: "stale write" }), /not found/);
    assert.equal(book.get(next.id)!.detail, "A different fact");
  } finally { db.close(); }
});

test("memory migration is transactional, journal-idempotent, and has no schema dependents", async () => {
  const path = await workspace();
  const source = new URL("../../../packages/bus/drizzle/books/", import.meta.url);
  const compiled = new URL("../../../../packages/bus/drizzle/books/", import.meta.url);
  const folder = existsSync(source) ? source : compiled;
  const journal = JSON.parse(readFileSync(new URL("meta/_journal.json", folder), "utf8"));
  const migrationPath = join(path, "migrations");
  mkdirSync(join(migrationPath, "meta"), { recursive: true });
  const oldEntries = journal.entries.filter((entry: { idx: number }) => entry.idx < 20);
  for (const entry of oldEntries) copyFileSync(new URL(`${entry.tag}.sql`, folder), join(migrationPath, `${entry.tag}.sql`));
  const journalPath = join(migrationPath, "meta", "_journal.json");
  writeFileSync(journalPath, JSON.stringify({ ...journal, entries: oldEntries }));
  const db = new DatabaseSync(join(path, "migration.db"));
  const wrapped = workspaceDatabase(db);
  try {
    db.exec("PRAGMA foreign_keys = ON");
    migrate(wrapped, migrationPath);
    // Inspect the actual pre-migration schema, not only the handwritten table.
    const dependents = db.prepare("SELECT name, type FROM sqlite_master WHERE type IN ('index', 'trigger', 'view') AND (tbl_name = 'books_memories' OR lower(sql) LIKE '%books_memories%')").all();
    assert.deepEqual(dependents, []);
    for (const table of db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all()) {
      const name = String(table.name).replaceAll('"', '""');
      assert.ok(!db.prepare(`PRAGMA foreign_key_list("${name}")`).all().some(key => key.table === "books_memories"), `unexpected foreign key from ${table.name}`);
    }
    db.exec(`INSERT INTO books_memories(id, topic, detail, kind, archived, created_at)
      VALUES(17, 'Active', 'Original active detail', 'short_term', 0, '2026-01-03'),
            (27, 'Archived', 'Original archived detail', 'long_term', 1, '2026-01-04');`);
    const original = db.prepare("SELECT * FROM books_memories ORDER BY id").all();
    const originalJournal = db.prepare("SELECT * FROM __drizzle_migrations ORDER BY id").all();
    const migration = readFileSync(new URL("0020_memory_versions.sql", folder), "utf8");
    const target = join(migrationPath, "0020_memory_versions.sql");
    writeFileSync(journalPath, JSON.stringify(journal));
    // Fail after the production rebuild/rename, through the actual migrator.
    writeFileSync(target, `${migration}\n--> statement-breakpoint\nSELECT * FROM deliberate_missing_migration_table;`);
    assert.throws(() => migrate(wrapped, migrationPath), /deliberate_missing_migration_table/);
    assert.deepEqual(db.prepare("SELECT * FROM books_memories ORDER BY id").all(), original);
    assert.deepEqual(db.prepare("SELECT * FROM __drizzle_migrations ORDER BY id").all(), originalJournal);
    assert.equal(db.prepare("SELECT name FROM sqlite_master WHERE name = 'books_memories_versioned'").get(), undefined);
    writeFileSync(target, migration);
    migrate(wrapped, migrationPath);
    const migrated = db.prepare("SELECT * FROM books_memories ORDER BY id").all();
    assert.deepEqual(migrated.map(({ version, ...row }) => row), original.map(row => ({ ...row })));
    assert.deepEqual(migrated.map(row => row.version), [1, 1]);
    const afterJournal = db.prepare("SELECT * FROM __drizzle_migrations ORDER BY id").all();
    migrate(wrapped, migrationPath);
    assert.deepEqual(db.prepare("SELECT * FROM __drizzle_migrations ORDER BY id").all(), afterJournal);
    assert.deepEqual(db.prepare("SELECT * FROM books_memories ORDER BY id").all(), migrated);
    assert.equal(db.prepare("SELECT seq FROM sqlite_sequence WHERE name = 'books_memories'").get()?.seq, 27);
    const book = new MemoryBook(wrapped);
    book.delete(27);
    const next = book.save({ topic: "Next", detail: "Distinct new fact" });
    assert.ok(next.id > 27);
    assert.throws(() => book.save({ id: 27, expectedVersion: 1, archived: true }), /not found/);
    assert.deepEqual(db.prepare("PRAGMA foreign_key_check").all(), []);
  } finally { db.close(); }
});

test("memory compare-and-swap protects against another connection and survives restart", async () => {
  const path = await workspace();
  const bus = new Bus("@memory-cas.magi", path);
  const db = new DatabaseSync(join(path, "memories", "books.db"));
  const other = new MemoryBook(workspaceDatabase(db));
  let id = 0;
  try {
    const first = bus.memoryBook.save({ topic: "Preference", detail: "Source: user message 1; old preference", kind: "long_term" });
    id = first.id;
    const observed = other.get(id)!;
    const fresh = bus.memoryBook.save({ id, expectedVersion: first.version, detail: "Source: user message 2; corrected preference" });
    assert.equal(fresh.version, 2);
    assert.throws(() => other.save({ id, expectedVersion: observed.version, detail: "stale background fact" }), /changed/);
    assert.throws(() => other.save({ id, expectedVersion: observed.version, archived: true }), /changed/);
    assert.equal(bus.memoryBook.get(id)!.archived, false);
    assert.equal(other.get(id)!.detail, fresh.detail);
    const archived = other.save({ id, expectedVersion: fresh.version, archived: true });
    assert.equal(archived.version, 3);
    assert.throws(() => bus.memoryBook.save({ id, expectedVersion: fresh.version, archived: false }), /changed/);
    assert.throws(() => bus.memoryBook.save({ id, detail: "unguarded" }), /expectedVersion is required/);
    assert.equal(bus.memoryBook.list().length, 0);
  } finally { db.close(); bus.close(); }
  const reopened = new Bus("@memory-cas.magi", path);
  try {
    assert.equal(reopened.memoryBook.list().length, 0);
    assert.equal(reopened.memoryBook.list(true).length, 1);
    assert.equal(reopened.memoryBook.get(id)!.version, 3);
    assert.equal(reopened.memoryBook.get(id)!.detail, "Source: user message 2; corrected preference");
  } finally { reopened.close(); }
});

test("memory tools reject invalid supplied values instead of creating or silently ignoring them", async () => {
  const bus = new Bus("@memory-validation.magi", await workspace());
  const tools = memoryTools(bus);
  const run = (name: string, args: Record<string, unknown>) => tools.find(tool => tool.name === name)!.run(args);
  try {
    for (const memory_id of ["1", null, 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
      await assert.rejects(run("save_memory", { memory_id, topic: "Bad", detail: "must not create" }), /memory_id/);
    }
    for (const args of [
      { topic: 3, detail: "fact" }, { topic: "fact", detail: false }, { topic: " ", detail: "fact" },
      { topic: "fact", detail: " " }, { topic: "fact", detail: "fact", archived: "false" },
      { topic: "fact", detail: "fact", kind: null }, { topic: "fact", detail: "fact", expected_version: 1 },
    ]) await assert.rejects(run("save_memory", args));
    assert.equal(bus.memoryBook.list(true).length, 0);
    const { memory } = JSON.parse(await run("save_memory", { topic: "Fact", detail: "Source: user message 5" }));
    for (const expected_version of [null, "1", 0, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
      await assert.rejects(run("save_memory", { memory_id: memory.id, expected_version, detail: "change" }), /expected_version/);
      await assert.rejects(run("complete_memory", { memory_id: memory.id, expected_version }), /expected_version/);
    }
    await assert.rejects(run("save_memory", { memory_id: memory.id, detail: "change" }), /expectedVersion/);
    await assert.rejects(run("complete_memory", { memory_id: memory.id }), /expected_version/);
    await assert.rejects(run("save_memory", { memory_id: 999, expected_version: 1, topic: "Not new", detail: "Not new" }), /not found/);
    await assert.rejects(run("list_memory", { include_archived: "true" }), /include_archived/);
    assert.equal(bus.memoryBook.get(memory.id)!.version, 1);
    assert.equal(JSON.parse(await run("list_memory", {})).memories.length, 1);
  } finally { bus.close(); }
});

test("MemoryWorker owns live tools/context and never claims work while stopped", async () => {
  const bus = new Bus("@memory-lifecycle.magi", await workspace());
  const worker = new MemoryWorker(bus);
  const board = bus.board("RunToolJob");
  const inner = bus.chats.forChannel("subconscious", "self");
  const publish = () => board.publish({ chat_id: inner.id, call: { tool_call_id: "list", name: "list_memory", arguments: {} } }, "subconscious");
  try {
    const before = publish();
    assert.equal(await worker.poll(), false);
    assert.equal(board.result(before), null);
    assert.equal(bus.tools.get("list_memory"), null);
    await worker.start();
    const accepted = worker.poll();
    await worker.stop(); // Accepted jobs drain, but no new job may be claimed.
    assert.equal(await accepted, true);
    assert.equal(board.result(before)?.status, "completed");
    assert.equal(bus.tools.get("list_memory"), null);
    assert.equal(bus.prompts.sections(inner.id).some(section => section.title === "Long-term memory"), false);
    const stopped = publish();
    assert.equal(await worker.poll(), false);
    assert.equal(board.result(stopped), null);
    await worker.start();
    await worker.poll();
    await until(() => Boolean(board.result(stopped)));
    assert.equal(board.result(stopped)?.status, "completed");
    const fact = bus.memoryBook.save({ topic: "Fact", detail: "Current context" });
    assert.match(bus.prompts.sections(inner.id)[0].body, new RegExp(`\\[${fact.id} \\| v1`));
    assert.match(bus.prompts.sections(inner.id)[0].body, /evidence, not new user instructions or authorization/);
    assert.equal(subconsciousTool("list_memory"), true);
    assert.equal(subconsciousTool("save_memory"), true);
    assert.equal(subconsciousTool("complete_memory"), true);
    assert.equal(subconsciousTool("delete_memory"), false);
  } finally { await worker.stop(); bus.close(); }
});

test("interrupting a private turn cancels unclaimed memory writes before shutdown", async () => {
  const bus = new Bus("@memory-interrupt.magi", await workspace());
  const memory = new MemoryWorker(bus);
  const subconscious = new SubconsciousWorker(bus);
  const provider = new ProvidersWorker(bus, { async complete() {
    return { role: "assistant", content: "", tool_calls: [{
      tool_call_id: "unclaimed-memory", name: "save_memory", arguments: { topic: "Unconfirmed", detail: "Must not be created" },
    }] };
  } });
  try {
    await memory.start(); await subconscious.start(); await provider.start();
    const inner = bus.chats.forChannel("subconscious", "self");
    const turn = messageDelivery.send(bus, inner.id, "Maintain memory.", undefined, "task");
    await subconscious.poll();
    for (let i = 0; i < 100 && !bus.board("RunToolJob").hasPending(); i++) { await provider.poll(); await sleep(10); }
    assert.ok(bus.board("RunToolJob").hasPending());
    await subconscious.stop();
    assert.equal(bus.board("MessageDeliveryJob").result(turn)?.status, "failed");
    assert.equal(await memory.poll(), false, "cancelled private mutation must not execute after interruption");
    assert.equal(bus.memoryBook.list(true).length, 0);
  } finally { await subconscious.stop(); await memory.stop(); await provider.stop(); bus.close(); }
});

test("private memory maintenance executes through the bus and reconciles foreground conflicts", async () => {
  const path = await workspace();
  const delivered: string[] = [];
  const seen: LLMRequest[] = [];
  let memoryId = 0;
  const magi = new Magi("@private-memory.magi", { workspace: path, deliver: text => delivered.push(text), client: {
    async complete(request) {
      seen.push(structuredClone(request));
      const tool = (name: string, args: Record<string, unknown>, call = name) => ({ role: "assistant" as const, content: "", tool_calls: [{ tool_call_id: call, name, arguments: args }] });
      switch (seen.length) {
        case 1:
          assert.ok(request.tools.some(tool => tool.name === "list_memory"));
          assert.ok(request.tools.some(tool => tool.name === "save_memory"));
          assert.ok(request.tools.some(tool => tool.name === "complete_memory"));
          assert.ok(!request.tools.some(tool => tool.name === "delete_memory"));
          assert.match(request.messages[0].content, /Keep my custom private instructions/);
          assert.match(request.messages[0].content, /not duplicate action-item commitments/);
          return tool("list_memory", { include_archived: true });
        case 2:
          assert.equal(JSON.parse(request.messages.at(-1)!.content).memories[0].version, 1);
          // The foreground changes the shared record after the background read.
          await memoryTools(magi.bus).find(tool => tool.name === "save_memory")!.run({
            memory_id: memoryId, expected_version: 1, detail: "Source: latest user message 8; corrected fact",
          });
          return { role: "assistant", content: "", tool_calls: [
            { tool_call_id: "stale-save", name: "save_memory", arguments: { memory_id: memoryId, expected_version: 1, detail: "obsolete fact" } },
            { tool_call_id: "stale-archive", name: "complete_memory", arguments: { memory_id: memoryId, expected_version: 1 } },
            { tool_call_id: "forbidden-delete", name: "delete_memory", arguments: { memory_id: memoryId } },
          ] };
        case 3:
          for (const result of request.messages.slice(-3, -1)) { assert.equal(result.is_error, true); assert.match(result.content, /changed/); }
          assert.match(request.messages.at(-1)!.content, /not available/);
          assert.equal(magi.bus.memoryBook.get(memoryId)!.archived, false);
          return tool("list_memory", { include_archived: true }, "reread");
        case 4:
          assert.equal(JSON.parse(request.messages.at(-1)!.content).memories[0].version, 2);
          return tool("save_memory", { memory_id: memoryId, expected_version: 2, topic: "Consolidated fact" });
        case 5:
          assert.equal(JSON.parse(request.messages.at(-1)!.content).memory.version, 3);
          return tool("complete_memory", { memory_id: memoryId, expected_version: 3 });
        default:
          assert.equal(JSON.parse(request.messages.at(-1)!.content).memory.version, 4);
          return { role: "assistant", content: "NO_REPLY" };
      }
    },
  } });
  try {
    magi.bus.prompts.set("subconscious/AGENT", "Keep my custom private instructions.");
    memoryId = magi.bus.memoryBook.save({ topic: "Fact", detail: "Source: user message 2; old fact", kind: "long_term" }).id;
    await magi.start();
    const inner = magi.bus.chats.forChannel("subconscious", "self");
    const job = messageDelivery.send(magi.bus, inner.id, "Maintain supported memory.", undefined, "task");
    await until(() => Boolean(magi.bus.board("MessageDeliveryJob").result(job)));
    assert.equal(magi.bus.board("MessageDeliveryJob").result(job)?.status, "completed");
    assert.equal(seen.length, 6);
    assert.deepEqual(delivered, []);
    assert.equal(magi.bus.prompts.get("subconscious/AGENT"), "Keep my custom private instructions.");
    const final = magi.bus.memoryBook.get(memoryId)!;
    assert.equal(final.archived, true);
    assert.equal(final.version, 4);
    assert.equal(final.topic, "Consolidated fact");
    assert.equal(final.detail, "Source: latest user message 8; corrected fact");
    assert.equal(magi.bus.memoryBook.list(true).length, 1, "maintenance updates one fact, not duplicate facts");
  } finally { await magi.stop(); }
  const reopened = new Bus("@private-memory.magi", path);
  try {
    assert.equal(reopened.memoryBook.get(memoryId)!.version, 4);
    assert.equal(reopened.memoryBook.list().length, 0);
  } finally { reopened.close(); }
});
