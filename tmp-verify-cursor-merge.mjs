// Temporary check for 0006_chat_channel_cursor.sql. Deleted right after it runs.
import { createRequire } from "node:module";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const require = createRequire("/Users/shichuan/.magi/MAGI/package.json");
const Database = require("better-sqlite3");
const { Bus } = await import("/Users/shichuan/.magi/MAGI/packages/bus/dist/index.js");

const journal = [
  ["0000_init", 1790292843675], ["0001_drop_sse_read_timeout", 1790297739646],
  ["0002_rebase_contact_ids", 1790299734163], ["0003_contact_channel_ids", 1790315055919],
  ["0004_chat_members", 1790316177145], ["0005_channel_cursors", 1790317213055],
];

const workspace = mkdtempSync(join(tmpdir(), "magi-upgrade-"));
mkdirSync(join(workspace, "memories"), { recursive: true });
const db = new Database(join(workspace, "memories", "magi.db"));
db.exec(`
  CREATE TABLE books_chats (
    id integer PRIMARY KEY NOT NULL, channel text NOT NULL, delivery_address text NOT NULL,
    instruction text DEFAULT '' NOT NULL, topic text DEFAULT '' NOT NULL,
    info text DEFAULT '' NOT NULL, summary text DEFAULT '' NOT NULL);
  CREATE UNIQUE INDEX books_chats_channel_address ON books_chats (channel, delivery_address);
  CREATE TABLE books_channel_cursors (
    id integer PRIMARY KEY NOT NULL, channel text NOT NULL, address text NOT NULL,
    last_sequence integer DEFAULT 0 NOT NULL);
  CREATE UNIQUE INDEX books_channel_cursors_target ON books_channel_cursors (channel, address);
  CREATE TABLE __drizzle_migrations (id SERIAL PRIMARY KEY, hash text NOT NULL, created_at numeric);
`);
db.prepare("INSERT INTO books_chats (id, channel, delivery_address) VALUES (1, 'asp', 'c1')").run();
db.prepare("INSERT INTO books_channel_cursors (channel, address, last_sequence) VALUES ('asp', 'c1', 7), ('asp', 'ghost', 3)").run();
for (const [, when] of journal) db.prepare("INSERT INTO __drizzle_migrations (hash, created_at) VALUES ('x', ?)").run(when);
db.close();

const bus = new Bus("@upgrade.magi", workspace);
const check = (name, actual, expected) => {
  if (actual !== expected) throw new Error(`${name}: expected ${expected}, got ${actual}`);
  console.log(`ok ${name} = ${actual}`);
};
check("reading survives the upgrade", bus.chats.read("asp", "c1"), 7);
check("reading is on the chat", bus.chats.forChannel("asp", "c1").last_sequence, 7);
check("chatless cursor is gone", bus.chats.read("asp", "ghost"), -1);
check("a fresh chat starts unread", bus.chats.forChannel("asp", "new").last_sequence, -1);
check("reading never goes backwards", (bus.chats.markRead("asp", "c1", 4), bus.chats.read("asp", "c1")), 7);
check("markRead advances it", (bus.chats.markRead("asp", "c1", 9), bus.chats.read("asp", "c1")), 9);
bus.close();

const after = new Database(join(workspace, "memories", "magi.db"), { readonly: true });
const tables = after.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all().map((row) => row.name);
after.close();
check("old table is dropped", tables.includes("books_channel_cursors"), false);
check("chats table is back", tables.includes("books_chats"), true);

// A workspace that lost its bookkeeping replays every migration: it must not throw.
const raw = new Database(join(workspace, "memories", "magi.db"));
raw.exec("DROP TABLE __drizzle_migrations");
raw.close();
const replayed = new Bus("@upgrade.magi", workspace);
replayed.close();
console.log("ok replaying every migration is harmless");

rmSync(workspace, { recursive: true, force: true });
console.log("done");
