import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

test("direct peer schema preserves contact identities and group history", () => {
  const db = new DatabaseSync(":memory:");
  try {
    db.exec(`PRAGMA foreign_keys = ON;
      CREATE TABLE books_contacts(id INTEGER PRIMARY KEY, name TEXT, role TEXT, asp_handle TEXT, peer_url TEXT, peer_trust TEXT);
      CREATE UNIQUE INDEX books_contacts_asp_handle_unique ON books_contacts(asp_handle);
      CREATE TABLE books_groups(conversation_uuid TEXT PRIMARY KEY, title TEXT, trust TEXT);
      CREATE TABLE history(id INTEGER PRIMARY KEY, contact_id INTEGER REFERENCES books_contacts(id), group_id TEXT REFERENCES books_groups(conversation_uuid), content TEXT);
      INSERT INTO books_contacts VALUES(7, 'saved contact', 'friend', '@saved.magi', 'http://saved.lan', 'pending');
      INSERT INTO books_groups VALUES('conversation', 'Saved group', 'pending');
      INSERT INTO history VALUES(9, 7, 'conversation', 'Keep this message');`);
    const source = new URL("../drizzle/books/0013_direct_peer_schema.sql", import.meta.url);
    const compiled = new URL("../../drizzle/books/0013_direct_peer_schema.sql", import.meta.url);
    const migration = readFileSync(existsSync(source) ? source : compiled, "utf8");
    db.exec(migration);
    assert.deepEqual({ ...db.prepare("SELECT * FROM books_contacts").get() }, {
      id: 7, name: "saved contact", role: "friend", handle: "@saved.magi", peer_url: "http://saved.lan",
    });
    assert.deepEqual({ ...db.prepare("SELECT * FROM books_groups").get() }, { conversation_uuid: "conversation", title: "Saved group" });
    assert.equal(db.prepare("SELECT content FROM history").get()?.content, "Keep this message");
    assert.equal(db.prepare("PRAGMA foreign_key_check").all().length, 0);
    assert.throws(() => db.exec("INSERT INTO books_contacts(id, handle) VALUES(8, '@saved.magi')"), /UNIQUE/);
  } finally { db.close(); }
});
