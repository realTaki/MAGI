import { and, desc, eq } from "drizzle-orm";
import type { BooksDb } from "../database.js";
import { contactNotes } from "../schema.js";

export type ContactNote = typeof contactNotes.$inferSelect;
export type NoteKind = ContactNote["kind"];

export class ContactNoteBook {
  constructor(private readonly db: BooksDb) {}

  get(id: number): ContactNote | null {
    return this.db.select().from(contactNotes).where(eq(contactNotes.id, id)).get() ?? null;
  }

  list(contactId: number, kind?: NoteKind): ContactNote[] {
    return this.db.select().from(contactNotes)
      .where(and(eq(contactNotes.contact_id, contactId), kind === undefined ? undefined : eq(contactNotes.kind, kind)))
      .orderBy(desc(contactNotes.id))
      .all();
  }

  save(input: { id?: number; contact_id?: number; note: string; kind?: NoteKind }): ContactNote {
    if (!input.note.trim()) throw new Error("contact note is required");
    if (input.id === undefined) {
      if (!input.contact_id) throw new Error("contact_id is required");
      return this.db.insert(contactNotes)
        .values({ contact_id: input.contact_id, note: input.note.trim(), kind: input.kind ?? "permanent" })
        .returning().get();
    }
    const current = this.get(input.id);
    if (!current) throw new Error(`contact_note ${input.id} not found`);
    return this.db.update(contactNotes)
      .set({ note: input.note.trim(), kind: input.kind ?? current.kind })
      .where(eq(contactNotes.id, input.id))
      .returning().get();
  }

  delete(id: number): boolean {
    // bun:sqlite reports no row count, so ask for the deleted row back instead.
    return this.db.delete(contactNotes).where(eq(contactNotes.id, id)).returning({ id: contactNotes.id }).get() !== undefined;
  }
}
