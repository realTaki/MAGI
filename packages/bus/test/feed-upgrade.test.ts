import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { Bus } from "../bus.js";
import { migrate } from "../sqlite.js";
import { workspaceDatabase } from "../drizzle/database.js";

test("published core 18/20 upgrades to Feed 21 without losing notes or repeating migrations", () => {
  const root = mkdtempSync(join(tmpdir(), "magi-feed-upgrade-"));
  const oldMigrations = join(root, "old-migrations");
  const source = new URL("../../drizzle/books/", import.meta.url);
  const journal = JSON.parse(readFileSync(new URL("meta/_journal.json", source), "utf8")) as { entries: { idx: number; tag: string; when: number }[] };
  assert.deepEqual(journal.entries.filter(entry => entry.idx >= 18).map(entry => entry.idx), [18, 20, 21]);
  assert.ok(journal.entries.every((entry, i, all) => i === 0 || all[i - 1]!.when < entry.when));
  const previous = { ...journal, entries: journal.entries.filter(entry => entry.idx < 21) };
  mkdirSync(join(oldMigrations, "meta"), { recursive: true });
  mkdirSync(join(root, "memories"));
  writeFileSync(join(oldMigrations, "meta", "_journal.json"), JSON.stringify(previous));
  for (const entry of previous.entries) writeFileSync(join(oldMigrations, `${entry.tag}.sql`), readFileSync(new URL(`${entry.tag}.sql`, source)));
  const old = new DatabaseSync(join(root, "memories", "books.db"));
  try {
    migrate(workspaceDatabase(old), oldMigrations);
    old.exec("INSERT INTO books_action_items (id, content, category, archived, version, created_at, updated_at) VALUES (73, 'Long goal', 'goal', 0, 8, 'created', 'updated')");
    old.exec("INSERT INTO books_memories (id, topic, detail, kind, archived, version, created_at) VALUES (91, 'Fact', 'Evidence', 'long_term', 1, 4, 'earlier')");
  } finally { old.close(); }
  try {
    let savedId = 0;
    for (let open = 0; open < 2; open++) {
      const bus = new Bus("@feed-upgrade.magi", root);
      try {
        assert.deepEqual(bus.actionItems.get(73), { id: 73, content: "Long goal", category: "goal", archived: false, version: 8, created_at: "created", updated_at: "updated" });
        assert.deepEqual(bus.memoryBook.get(91), { id: 91, topic: "Fact", detail: "Evidence", kind: "long_term", archived: true, version: 4, created_at: "earlier" });
        if (open === 0) {
          const evidence = bus.feed.recordEvidence("fake_search", 123, "Actual result https://example.org/seen", { source_fingerprint: "fake-v1", config_revision: 1 });
          savedId = bus.feed.save({ title: "Discovery", body: "A supported result", why_relevant: "Matches saved interest", sources: [{ evidence_id: evidence.id, url: "https://example.org/seen" }] }).id;
        }
        assert.equal(bus.feed.get(savedId)?.title, "Discovery");
        assert.equal(bus.feed.list().length, 1);
      } finally { bus.close(); }
    }
  } finally { rmSync(root, { recursive: true, force: true }); }
});
