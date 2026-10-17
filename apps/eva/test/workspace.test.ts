import { afterEach, expect, test } from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { Bus, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, messageDelivery } from "@magi/bus";

const workspaces: string[] = [];
afterEach(async () => { for (const path of workspaces.splice(0)) await rm(path, { recursive: true, force: true }); });
async function workspace() { const path = await mkdtemp(join(tmpdir(), "magi-workspace-")); workspaces.push(path); return path; }

function tables(path: string, database: string): string[] {
  const db = new DatabaseSync(join(path, database), { readOnly: true });
  const rows = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all() as Array<{ name: string }>;
  db.close();
  return rows.map((row) => row.name);
}

test("a fresh workspace gets every Book table from the migrations", async () => {
  const path = await workspace();
  const bus = new Bus("@fresh.magi", path);
  try {
    expect(tables(path, "memories/books.db")).toEqual(expect.arrayContaining([
      "books_contacts", "books_contact_notes", "books_chat_members",
      "books_chats", "books_mcp_servers", "books_memories", "books_messages", "books_settings",
      "books_tasks",
    ]));
    // The channel reading position is a field on the chat now, not a table of its own.
    expect(tables(path, "memories/books.db")).not.toContain("books_channel_cursors");
    expect(tables(path, "logs/jobs.db")).toContain("jobs");
  } finally { bus.close(); }
});

test("Book references reject rows without their chat or contact", async () => {
  const path = await workspace();
  const bus = new Bus("@references.magi", path);
  try {
    const chat = bus.chats.forChannel("cli", "terminal");
    expect(() => bus.messages.add(chat.id + 1, SYSTEM_CONTACT_ID, "orphan")).toThrow("FOREIGN KEY");
    expect(() => bus.messages.add(chat.id, 999, "orphan")).toThrow("FOREIGN KEY");
    expect(() => bus.chatMembers.add(chat.id + 1, SYSTEM_CONTACT_ID)).toThrow("FOREIGN KEY");
    expect(() => bus.tasks.save({ name: "orphan", prompt: "no chat", cron: "* * * * *", chat_id: chat.id + 1 })).toThrow("FOREIGN KEY");
  } finally { bus.close(); }
});

test("a notice reaches the operator's home chat", async () => {
  const path = await workspace();
  const bus = new Bus("@notice.magi", path);
  try {
    expect(messageDelivery.notify(bus, "nobody to tell yet")).toBeNull();
    const home = bus.chats.forChannel("cli", "terminal");
    messageDelivery.setHomeChat(bus, home.id);
    expect(messageDelivery.homeChat(bus)).toBe(home.id);

    messageDelivery.notify(bus, "[mcp] demo: connect refused");
    expect(bus.messages.list(home.id).at(-1)?.content).toBe("[mcp] demo: connect refused");

    const elsewhere = bus.chats.forChannel("cli", "other");
    messageDelivery.notify(bus, "just here", elsewhere.id);
    expect(bus.messages.list(elsewhere.id).at(-1)?.content).toBe("just here");
  } finally {
    bus.close();
    await rm(path, { recursive: true, force: true });
  }
});

test("one message job records both directions and carries only the message id", async () => {
  const path = await workspace();
  const bus = new Bus("@record.magi", path);
  try {
    const chat = bus.chats.forChannel("cli", "terminal");
    // Received: the row is the durable fact, the job is only the wake-up call, and the
    // author the chat does not name is the operator.
    messageDelivery.send(bus, chat.id, "hello");
    const received = bus.messages.list(chat.id).at(-1)!;
    expect(received).toMatchObject({ contact_id: SYSTEM_CONTACT_ID, content: "hello" });
    expect(bus.board("MessageDeliveryJob").claim("test")?.input)
      .toEqual({ message_id: received.id });
    // A message asked of someone else is kept as history, and nobody delivers it.
    messageDelivery.record(bus, chat.id, "not for me");
    expect(bus.messages.list(chat.id).at(-1)?.content).toBe("not for me");
    expect(bus.board("MessageDeliveryJob").claim("test")).toBeNull();
    // Sent: this MAGI's own words, so a channel is the one that acts on it.
    messageDelivery.send(bus, chat.id, "reply", MAGI_CONTACT_ID, "test");
    const sent = bus.messages.list(chat.id).at(-1)!;
    expect(sent).toMatchObject({ contact_id: MAGI_CONTACT_ID, content: "reply" });
    expect(bus.board("MessageDeliveryJob").claim("test")?.input)
      .toEqual({ message_id: sent.id });
  } finally {
    bus.close();
    await rm(path, { recursive: true, force: true });
  }
});


