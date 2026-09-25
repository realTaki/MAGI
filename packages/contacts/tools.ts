/** Contacts and the notes attached to them. */

import type { Bus, ContactRole, ExecutableTool, NoteKind } from "@magi/bus";

/** Local on purpose: this package must not depend on the tools package. */
function stringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  if (typeof value !== "string" || !value) throw new Error(`${key} must be a non-empty string`);
  return value;
}

/** Local on purpose: this package must not depend on the tools package. */
function integerArg(args: Record<string, unknown>, key: string): number {
  const value = args[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) throw new Error(`${key} must be a positive integer`);
  return value;
}

/** Local on purpose: this package must not depend on the tools package. */
function boundedInteger(value: unknown, key: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) throw new Error(`${key} must be an integer from ${min} to ${max}`);
  return value;
}

/** Local on purpose: this package must not depend on the tools package. */
function optionalBoundedInteger(value: unknown, fallback: number, key: string, min: number, max: number): number {
  return value === undefined ? fallback : boundedInteger(value, key, min, max);
}

function contactRole(value: unknown): ContactRole {
  const role = typeof value === "string" ? value.trim().toLowerCase() : "stranger";
  if (role === "assigned") return "authorized";
  if (role === "guest") return "stranger";
  if (role === "authorized" || role === "stranger" || role === "magi" || role === "third_party_agent") return role;
  throw new Error("role must be authorized, stranger, magi, or third_party_agent");
}

function noteKind(value: unknown): NoteKind {
  if (value === "permanent" || value === "daily") return value;
  throw new Error("kind must be permanent or daily");
}

export function contactTools(bus: Bus): ExecutableTool[] {
  return [
    {
      name: "add_contact", description: "Create a contact, optionally with an initial permanent note.",
      input_schema: { type: "object", properties: {
        name: { type: "string" }, nickname: { type: "string" },
        role: { type: "string", enum: ["assigned", "authorized", "guest", "stranger", "magi", "third_party_agent"] },
        notes: { type: "string" },
      }, required: ["name"] },
      async run(args) {
        const contact = bus.contacts.create({
          name: stringArg(args, "name"),
          nickname: typeof args.nickname === "string" ? args.nickname : undefined,
          role: contactRole(args.role),
        });
        const note = typeof args.notes === "string" && args.notes.trim()
          ? bus.contactNotes.save({ contact_id: contact.id, note: args.notes, kind: "permanent" }) : undefined;
        return JSON.stringify({ created: contact, initial_note: note });
      },
    },
    {
      name: "save_contact_note", description: "Create or update a permanent or daily note about a contact.",
      input_schema: { type: "object", properties: {
        contact_id: { type: "integer" }, note_id: { type: "integer" }, note: { type: "string" },
        kind: { type: "string", enum: ["permanent", "daily"] },
      }, required: ["note"] },
      async run(args) {
        const note = stringArg(args, "note");
        const kind = args.kind === undefined ? undefined : noteKind(args.kind);
        if (args.note_id !== undefined) {
          const updated = bus.contactNotes.save({ id: integerArg(args, "note_id"), note, kind });
          return JSON.stringify({ updated });
        }
        const contactId = integerArg(args, "contact_id");
        if (!bus.contacts.get(contactId)) throw new Error(`contact ${contactId} not found`);
        return JSON.stringify({ created: bus.contactNotes.save({ contact_id: contactId, note, kind }) });
      },
    },
    {
      name: "delete_contact_note", description: "Delete a contact note by id. Missing notes are a no-op.",
      input_schema: { type: "object", properties: { note_id: { type: "integer" } }, required: ["note_id"] },
      async run(args) {
        const noteId = integerArg(args, "note_id");
        return JSON.stringify({ note_id: noteId, existed: bus.contactNotes.delete(noteId) });
      },
    },
    {
      name: "search_contacts", description: "Search contacts by name, nickname, or note text.",
      input_schema: { type: "object", properties: {
        query: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 100 },
        notes_per_contact: { type: "integer", minimum: 0, maximum: 50 },
      }, required: ["query"] },
      async run(args) {
        const query = stringArg(args, "query");
        const needle = query.toLowerCase();
        const limit = optionalBoundedInteger(args.limit, 20, "limit", 1, 100);
        const notesPerContact = optionalBoundedInteger(args.notes_per_contact, 5, "notes_per_contact", 0, 50);
        const contacts = bus.contacts.list().flatMap((contact) => {
          const notes = bus.contactNotes.list(contact.id);
          if (![contact.name, contact.nickname ?? "", ...notes.map((item) => item.note)].some((value) => value.toLowerCase().includes(needle))) return [];
          return [{ ...contact, notes: notes.slice(0, notesPerContact) }];
        }).slice(0, limit);
        return JSON.stringify({ query, contacts });
      },
    },
    {
      name: "update_daily_note", description: "Append one meaningful fact to a contact's daily note.",
      input_schema: { type: "object", properties: {
        body_delta: { type: "string" }, contact_id: { type: "integer" },
      }, required: ["body_delta", "contact_id"] },
      async run(args) {
        const delta = stringArg(args, "body_delta");
        const contactId = integerArg(args, "contact_id");
        if (!bus.contacts.get(contactId)) throw new Error(`contact ${contactId} not found`);
        const existing = bus.contactNotes.list(contactId, "daily")[0];
        const note = existing
          ? bus.contactNotes.save({ id: existing.id, note: `${existing.note.trimEnd()}\n${delta}`, kind: "daily" })
          : bus.contactNotes.save({ contact_id: contactId, note: delta, kind: "daily" });
        return JSON.stringify({ contact_note_id: note.id, created: !existing });
      },
    },
  ];
}
