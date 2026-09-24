import { afterEach, expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { Bus } from "../bus/index.js";

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
      "books_contacts", "books_contact_notes", "books_conversations", "books_mcp_servers",
      "books_memories", "books_messages", "books_settings", "books_tasks",
    ]));
    expect(tables(path, "logs")).toContain("jobs");
  } finally { bus.close(); }
});

test("adopts a workspace an earlier release created, keeping its data", async () => {
  const path = await workspace();
  const first = new Bus("@legacy.magi", path);
  try {
    first.contacts.create({ name: "Ada", role: "authorized" });
    first.settings.set("provider.name", "openai");
    const conversation = first.conversations.forChannel("cli", "legacy");
    first.messages.add(conversation.id, 0, "hello");
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
