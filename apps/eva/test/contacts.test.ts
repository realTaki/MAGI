import { expect, test } from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../eva.js";
import { sequentialMagiHandle } from "@magi/bus";
import { contactTools } from "@magi/contacts/tools.js";

test("contact tools persist, search, update, and delete notes", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-contact-"));
  const magi = new Magi("@contacts.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  const tools = new Map(contactTools(magi.bus).map((tool) => [tool.name, tool]));
  try {
    const added = JSON.parse(await tools.get("add_contact")!.run({
      name: "Ada Lovelace", nickname: "Ada", role: "authorized", notes: "Works on the analytical engine",
    })) as { created: { id: number }; initial_note: { id: number } };
    expect(added.created.id).toBeGreaterThan(1);
    expect(magi.bus.contactNotes.get(added.initial_note.id)?.kind).toBe("permanent");

    const firstDaily = JSON.parse(await tools.get("update_daily_note")!.run({
      contact_id: added.created.id, body_delta: "Reviewed the runtime",
    })) as { contact_note_id: number; created: boolean };
    expect(firstDaily.created).toBeTrue();
    const secondDaily = JSON.parse(await tools.get("update_daily_note")!.run({
      contact_id: added.created.id, body_delta: "Approved the worker design",
    })) as { contact_note_id: number; created: boolean };
    expect(secondDaily).toEqual({ contact_note_id: firstDaily.contact_note_id, created: false });

    const found = JSON.parse(await tools.get("search_contacts")!.run({ query: "worker design" })) as { contacts: Array<{ name: string }> };
    expect(found.contacts.map((contact) => contact.name)).toEqual(["Ada Lovelace"]);
    const deleted = JSON.parse(await tools.get("delete_contact_note")!.run({ note_id: added.initial_note.id }));
    expect(deleted).toEqual({ note_id: added.initial_note.id, existed: true });
    expect(magi.bus.contactNotes.get(added.initial_note.id)).toBeNull();
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("reserved system and MAGI contacts are seeded", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-contact-seed-"));
  const magi = new Magi("@seed.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  try {
    expect(magi.bus.contacts.get(1)).toMatchObject({ name: "system", role: "system", handle: "@user.magi" });
    expect(magi.bus.contacts.get(2)).toMatchObject({ name: "@seed.magi", role: "magi", handle: "@seed.magi" });
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("a channel identity belongs to one contact, learned when it first speaks", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-contact-identity-"));
  const magi = new Magi("@identity.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  try {
    const other = magi.bus.contacts.forHandle(sequentialMagiHandle(1));
    expect(other).toMatchObject({ name: sequentialMagiHandle(1), handle: sequentialMagiHandle(1), role: "magi" });
    expect(magi.bus.contacts.forHandle(sequentialMagiHandle(1)).id).toBe(other.id);

    const person = magi.bus.contacts.forTg("12345");
    expect(person).toMatchObject({ name: "tg:12345", tg_id: "12345", role: "stranger" });
    expect(magi.bus.contacts.forTg("12345").id).toBe(person.id);

    // One person, two channels: linking the second identity must not make a new contact.
    magi.bus.contacts.update(person.id, { handle: "@adam" });
    expect(magi.bus.contacts.forHandle("@adam").id).toBe(person.id);
    expect(magi.bus.contacts.forTg("12345").id).toBe(person.id);

    // A workspace from before identities were stored has the contact named after the
    // handle: it is remembered there instead of being duplicated.
    const named = magi.bus.contacts.create({ name: sequentialMagiHandle(2), role: "magi" });
    expect(magi.bus.contacts.forHandle(sequentialMagiHandle(2)).id).toBe(named.id);
    expect(magi.bus.contacts.get(named.id)?.handle).toBe(sequentialMagiHandle(2));
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("chat membership is a set, not a log", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-members-"));
  const magi = new Magi("@members.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  try {
    const chat = magi.bus.chats.forChannel("cli", "terminal");
    const guest = magi.bus.contacts.forHandle(sequentialMagiHandle(1));
    magi.bus.chatMembers.add(chat.id, guest.id);
    magi.bus.chatMembers.add(chat.id, guest.id);
    expect(magi.bus.chatMembers.list(chat.id).map((contact) => contact.id)).toEqual([guest.id]);

    magi.bus.chatMembers.remove(chat.id, guest.id);
    expect(magi.bus.chatMembers.list(chat.id)).toEqual([]);
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});
