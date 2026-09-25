import { afterEach, expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { Bus, SYSTEM_CONTACT_ID } from "../bus/index.js";

const workspaces: string[] = [];
afterEach(async () => { for (const path of workspaces.splice(0)) await rm(path, { recursive: true, force: true }); });
async function workspace() { const path = await mkdtemp(join(tmpdir(), "magi-workspace-")); workspaces.push(path); return path; }

function tables(path: string, database: string): string[] {
  const db = new Database(join(path, database, "magi.db"), { readonly: true });
  const rows = db.query("SELECT name FROM sqlite_master WHERE type = 'table'").all() as Array<{ name: string }>;
  db.close();
  return rows.map((row) => row.name);
}

test("a fresh workspace gets every Book table from the migrations", async () => {
  const path = await workspace();
  const bus = new Bus("@fresh.magi", path);
  try {
    expect(tables(path, "memories")).toEqual(expect.arrayContaining([
      "books_channel_cursors", "books_contacts", "books_contact_notes", "books_conversation_members",
      "books_conversations", "books_mcp_servers", "books_memories", "books_messages", "books_settings",
      "books_tasks",
    ]));
    expect(tables(path, "logs")).toContain("jobs");
  } finally { bus.close(); }
});

test("a notice reaches the operator's home conversation", async () => {
  const path = await workspace();
  const bus = new Bus("@notice.magi", path);
  try {
    expect(bus.publishNotice("nobody to tell yet")).toBeNull();
    const home = bus.conversations.forChannel("cli", "terminal");
    bus.setHomeConversation(home.id);
    expect(bus.homeConversation()).toBe(home.id);

    bus.publishNotice("[mcp] demo: connect refused");
    expect(bus.messages.list(home.id).at(-1)?.content).toBe("[mcp] demo: connect refused");

    const elsewhere = bus.conversations.forChannel("cli", "other");
    bus.publishNotice("just here", elsewhere.id);
    expect(bus.messages.list(elsewhere.id).at(-1)?.content).toBe("just here");
  } finally {
    bus.close();
    await rm(path, { recursive: true, force: true });
  }
});

test("adopts a workspace an earlier release created, keeping its data", async () => {
  const path = await workspace();
  const first = new Bus("@legacy.magi", path);
  try {
    first.contacts.create({ name: "Ada", role: "authorized" });
    first.settings.set("provider.name", "openai");
    const conversation = first.conversations.forChannel("cli", "legacy");
    first.messages.add(conversation.id, SYSTEM_CONTACT_ID, "hello");
  } finally { first.close(); }

  // What an earlier release left behind: the tables, but no migration bookkeeping.
  const dropped: string[] = [];
  for (const database of ["memories", "logs"]) {
    const db = new Database(join(path, database, "magi.db"), { create: true });
    for (const row of db.query("SELECT name FROM sqlite_master WHERE name LIKE '__drizzle%'").all() as Array<{ name: string }>) {
      db.exec(`DROP TABLE "${row.name}"`);
      dropped.push(`${database}:${row.name}`);
    }
    db.close();
  }
  expect(dropped).toHaveLength(2);

  const reopened = new Bus("@legacy.magi", path);
  try {
    expect(reopened.settings.get("provider.name")).toBe("openai");
    expect(reopened.contacts.list().map((contact) => contact.name)).toContain("Ada");
    expect(reopened.messages.list(reopened.conversations.forChannel("cli", "legacy").id)[0]?.content).toBe("hello");
    expect(tables(path, "memories")).toContain("__drizzle_migrations");
  } finally { reopened.close(); }
});

test("a workspace numbered from 0 gets its contacts renumbered from 1", async () => {
  const path = await workspace();
  const first = new Bus("@rebase.magi", path);
  const conversation = first.conversations.forChannel("cli", "legacy");
  // What a workspace from before the change holds: the system contact at 0, the MAGI
  // at 1, and messages pointing at both.
  const db = new Database(join(path, "memories", "magi.db"));
  db.exec("DELETE FROM books_messages");
  db.exec("DELETE FROM books_contacts");
  db.exec(`INSERT INTO books_contacts (id, name, role) VALUES (0, 'system', 'system'), (1, '@rebase.magi', 'magi'), (5, 'Ada', 'authorized')`);
  db.exec(`INSERT INTO books_messages (conversation_id, contact_id, content) VALUES (${conversation.id}, 0, 'from the operator'), (${conversation.id}, 1, 'from the magi')`);
  dropBookkeeping(path);
  first.close();

  const migrated = new Bus("@rebase.magi", path);
  try {
    expect(migrated.contacts.list().map((contact) => `${contact.id}:${contact.name}`)).toEqual(["1:system", "2:@rebase.magi", "5:Ada"]);
    expect(migrated.messages.list(conversation.id).map((message) => `${message.contact_id}:${message.content}`))
      .toEqual(["1:from the operator", "2:from the magi"]);
  } finally { migrated.close(); }

  // Losing the bookkeeping again must not renumber a second time.
  dropBookkeeping(path);
  const replayed = new Bus("@rebase.magi", path);
  try {
    expect(replayed.contacts.list().map((contact) => contact.id)).toEqual([1, 2, 5]);
    expect(replayed.messages.list(conversation.id).map((message) => message.contact_id)).toEqual([1, 2]);
  } finally { replayed.close(); }
});

function dropBookkeeping(path: string): void {
  for (const database of ["memories", "logs"]) {
    const db = new Database(join(path, database, "magi.db"), { create: true });
    for (const row of db.query("SELECT name FROM sqlite_master WHERE name LIKE '__drizzle%'").all() as Array<{ name: string }>) {
      db.exec(`DROP TABLE "${row.name}"`);
    }
    db.close();
  }
}
