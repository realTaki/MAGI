import type { Database } from "bun:sqlite";

export type ContactRole = "system" | "authorized" | "stranger" | "magi" | "third_party_agent";
export type Contact = { id: number; name: string; nickname: string | null; role: ContactRole; last_seen_at: string };

export class ContactBook {
  constructor(private readonly db: Database) {}

  get(id: number): Contact | null {
    return (this.db.prepare("SELECT * FROM books_contacts WHERE id = ?").get(id) as Contact | undefined) ?? null;
  }

  list(): Contact[] {
    return this.db.prepare("SELECT * FROM books_contacts ORDER BY id").all() as Contact[];
  }

  create(input: { name: string; nickname?: string; role?: ContactRole }): Contact {
    if (!input.name.trim()) throw new Error("contact name is required");
    try {
      const result = this.db.prepare("INSERT INTO books_contacts (name, nickname, role) VALUES (?, ?, ?)")
        .run(input.name.trim(), input.nickname?.trim() || null, input.role ?? "stranger");
      return this.get(Number(result.lastInsertRowid))!;
    } catch (error) {
      if (String(error).includes("UNIQUE")) throw new Error(`contact ${input.name.trim()} already exists`);
      throw error;
    }
  }

  update(id: number, input: { name?: string; nickname?: string | null; role?: ContactRole }): Contact {
    const current = this.get(id);
    if (!current) throw new Error(`contact ${id} not found`);
    this.db.prepare("UPDATE books_contacts SET name = ?, nickname = ?, role = ?, last_seen_at = CURRENT_TIMESTAMP WHERE id = ?")
      .run(input.name?.trim() || current.name, input.nickname === undefined ? current.nickname : input.nickname?.trim() || null, input.role ?? current.role, id);
    return this.get(id)!;
  }
}
