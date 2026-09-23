import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
import { builtinTools } from "../tools/registry.js";

test("contact tools persist, search, update, and delete notes", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "ts-magi-contact-"));
  const magi = new Magi("@contacts.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  const tools = new Map(builtinTools(magi.bus).map((tool) => [tool.name, tool]));
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
  const workspace = await mkdtemp(join(tmpdir(), "ts-magi-contact-seed-"));
  const magi = new Magi("@seed.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  try {
    expect(magi.bus.contacts.get(0)).toMatchObject({ name: "system", role: "system" });
    expect(magi.bus.contacts.get(1)).toMatchObject({ name: "@seed.magi", role: "magi" });
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});
