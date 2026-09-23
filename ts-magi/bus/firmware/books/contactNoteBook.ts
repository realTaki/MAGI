import type { Database } from "bun:sqlite";

export type NoteKind = "permanent" | "daily";
export type ContactNote = { id: number; contact_id: number; note: string; kind: NoteKind; created_at: string };

export class ContactNoteBook {
  constructor(private readonly db: Database) {}

  get(id: number): ContactNote | null {
    return (this.db.prepare("SELECT * FROM books_contact_notes WHERE id = ?").get(id) as ContactNote | undefined) ?? null;
  }

  list(contactId: number, kind?: NoteKind): ContactNote[] {
    return (kind === undefined
      ? this.db.prepare("SELECT * FROM books_contact_notes WHERE contact_id = ? ORDER BY id DESC").all(contactId)
      : this.db.prepare("SELECT * FROM books_contact_notes WHERE contact_id = ? AND kind = ? ORDER BY id DESC").all(contactId, kind)) as ContactNote[];
  }

  save(input: { id?: number; contact_id?: number; note: string; kind?: NoteKind }): ContactNote {
    if (!input.note.trim()) throw new Error("contact note is required");
    if (input.id === undefined) {
      if (!input.contact_id) throw new Error("contact_id is required");
      const result = this.db.prepare("INSERT INTO books_contact_notes (contact_id, note, kind) VALUES (?, ?, ?)")
        .run(input.contact_id, input.note.trim(), input.kind ?? "permanent");
      return this.get(Number(result.lastInsertRowid))!;
    }
    const current = this.get(input.id);
    if (!current) throw new Error(`contact_note ${input.id} not found`);
    this.db.prepare("UPDATE books_contact_notes SET note = ?, kind = ? WHERE id = ?")
      .run(input.note.trim(), input.kind ?? current.kind, input.id);
    return this.get(input.id)!;
  }

  delete(id: number): boolean {
    return this.db.prepare("DELETE FROM books_contact_notes WHERE id = ?").run(id).changes === 1;
  }
}
