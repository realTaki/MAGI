import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { Bus } from "../bus.js";
import { migrate } from "../sqlite.js";
import { workspaceDatabase } from "../drizzle/database.js";
import type { ActionItemCategory } from "../books/actionItemBook.js";

test("category migration preserves legacy rows, versions, timestamps and identity", () => {
  const db = new DatabaseSync(":memory:");
  const migration = (name: string) => readFileSync(new URL(`../../drizzle/books/${name}`, import.meta.url), "utf8");
  try {
    db.exec(migration("0014_action_items.sql"));
    db.exec(migration("0016_action_item_versions.sql"));
    db.exec("INSERT INTO books_action_items (id, content, archived, version, created_at, updated_at) VALUES (42, 'Saved note', 1, 7, 'created', 'updated')");
    db.exec(migration("0018_action_item_categories.sql"));
    assert.deepEqual({ ...db.prepare("SELECT * FROM books_action_items WHERE id = 42").get() }, {
      id: 42, content: "Saved note", archived: 1, version: 7, created_at: "created", updated_at: "updated", category: "todo",
    });
    db.exec("INSERT INTO books_action_items (content) VALUES ('Legacy insert')");
    assert.deepEqual({ ...db.prepare("SELECT id, category FROM books_action_items WHERE id = 43").get() }, { id: 43, category: "todo" });
    assert.throws(() => db.exec("INSERT INTO books_action_items (content, category) VALUES ('Invalid', 'invalid')"), /CHECK/);
    assert.throws(() => db.exec("UPDATE books_action_items SET category = NULL WHERE id = 42"), /NOT NULL/);
  } finally { db.close(); }
});

test("categories persist across restart with compatible defaults, filters and CAS updates", () => {
  const root = mkdtempSync(join(tmpdir(), "magi-categories-"));
  let bus = new Bus("@categories.magi", root);
  try {
    const todo = bus.actionItems.save({ content: "Finish review" });
    const idea = bus.actionItems.save({ content: "Maybe explore a different layout", category: "idea" });
    assert.equal(todo.category, "todo");
    assert.equal(bus.actionItems.seedOnce("legacy", "Existing preset")?.category, "todo");
    assert.deepEqual(bus.actionItems.list(false, "idea"), [idea]);
    assert.deepEqual(bus.actionItems.list(false, "todo").map(item => item.id), [todo.id, todo.id + 2]);
    const revised = bus.actionItems.save({ id: idea.id, expectedVersion: 1, content: "Maybe explore an accessible layout" });
    assert.equal(revised.category, "idea");
    assert.equal(revised.version, 2);
    assert.throws(() => bus.actionItems.save({ id: idea.id, expectedVersion: 1, category: "todo" }), /changed/);
    assert.deepEqual(bus.actionItems.get(idea.id), revised);
    const promoted = bus.actionItems.save({ id: idea.id, expectedVersion: 2, category: "todo" });
    assert.equal(promoted.content, revised.content);
    assert.equal(promoted.version, 3);
    const withdrawn = bus.actionItems.save({ id: idea.id, expectedVersion: 3, category: "idea", archived: true });
    assert.deepEqual(bus.actionItems.list(false, "idea"), []);
    assert.deepEqual(bus.actionItems.list(true, "idea"), [withdrawn]);
    for (const invalid of ["invalid", "", null, 1]) {
      assert.throws(() => bus.actionItems.save({ content: "Invalid", category: invalid as ActionItemCategory }), /category must/);
      assert.throws(() => bus.actionItems.list(true, invalid as ActionItemCategory), /category must/);
    }
    const goal = bus.actionItems.save({ category: "goal", content: "Background: ship accessible app. Success: audited flows. Evidence: design reviewed. Gap: implementation." });
    assert.deepEqual(bus.actionItems.list(false, "goal"), [goal]);
    bus.close();
    bus = new Bus("@categories.magi", root);
    assert.deepEqual(bus.actionItems.get(idea.id), withdrawn);
    assert.deepEqual(bus.actionItems.get(goal.id), goal);
    assert.equal(bus.actionItems.get(todo.id)?.category, "todo");
    const restored = bus.actionItems.save({ id: idea.id, expectedVersion: 4, archived: false });
    assert.equal(restored.category, "idea");
    assert.equal(restored.version, 5);
    assert.throws(() => bus.actionItems.delete(idea.id, 4), /changed/);
    assert.equal(bus.actionItems.delete(idea.id, 5), true);
  } finally { bus.close(); rmSync(root, { recursive: true, force: true }); }
});


test("opening an existing EVA migrates its Book once without rewriting old records", () => {
  const root = mkdtempSync(join(tmpdir(), "magi-category-upgrade-"));
  const oldMigrations = join(root, "old-migrations");
  mkdirSync(join(oldMigrations, "meta"), { recursive: true });
  mkdirSync(join(root, "memories"));
  const source = new URL("../../drizzle/books/", import.meta.url);
  const journal = JSON.parse(readFileSync(new URL("meta/_journal.json", source), "utf8")) as {
    entries: { idx: number; tag: string }[];
  };
  journal.entries = journal.entries.filter(entry => entry.idx < 18);
  writeFileSync(join(oldMigrations, "meta", "_journal.json"), JSON.stringify(journal));
  for (const entry of journal.entries) writeFileSync(join(oldMigrations, `${entry.tag}.sql`), readFileSync(new URL(`${entry.tag}.sql`, source)));
  const old = new DatabaseSync(join(root, "memories", "books.db"));
  try {
    migrate(workspaceDatabase(old), oldMigrations);
    old.exec("INSERT INTO books_action_items (id, content, version, archived, created_at, updated_at) VALUES (77, 'Existing archived note', 9, 1, 'before-created', 'before-updated')");
  } finally { old.close(); }
  try {
    for (let open = 0; open < 2; open++) {
      const bus = new Bus("@upgraded.magi", root);
      try {
        assert.deepEqual(bus.actionItems.get(77), {
          id: 77, content: "Existing archived note", category: "todo", version: 9,
          archived: true, created_at: "before-created", updated_at: "before-updated",
        });
        assert.deepEqual(bus.actionItems.list(), []);
        assert.equal(bus.actionItems.list(true, "todo").length, 1);
      } finally { bus.close(); }
    }
  } finally { rmSync(root, { recursive: true, force: true }); }
});
