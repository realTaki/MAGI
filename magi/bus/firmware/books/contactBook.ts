import { eq, sql } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../database.js";

export const contacts = sqliteTable("books_contacts", {
  id: integer("id").primaryKey(),
  name: text("name").notNull().unique(),
  nickname: text("nickname"),
  role: text("role").$type<"system" | "authorized" | "stranger" | "magi" | "third_party_agent">().notNull().default("stranger"),
  last_seen_at: text("last_seen_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
});

export type Contact = typeof contacts.$inferSelect;
export type ContactRole = Contact["role"];

export class ContactBook {
  constructor(private readonly db: BusDb) {}

  get(id: number): Contact | null {
    return this.db.select().from(contacts).where(eq(contacts.id, id)).get() ?? null;
  }

  list(): Contact[] {
    return this.db.select().from(contacts).orderBy(contacts.id).all();
  }

  create(input: { name: string; nickname?: string; role?: ContactRole }): Contact {
    if (!input.name.trim()) throw new Error("contact name is required");
    try {
      return this.db.insert(contacts)
        .values({ name: input.name.trim(), nickname: input.nickname?.trim() || null, role: input.role ?? "stranger" })
        .returning().get();
    } catch (error) {
      if (String(error).includes("UNIQUE")) throw new Error(`contact ${input.name.trim()} already exists`);
      throw error;
    }
  }

  update(id: number, input: { name?: string; nickname?: string | null; role?: ContactRole }): Contact {
    const current = this.get(id);
    if (!current) throw new Error(`contact ${id} not found`);
    return this.db.update(contacts)
      .set({
        name: input.name?.trim() || current.name,
        nickname: input.nickname === undefined ? current.nickname : input.nickname?.trim() || null,
        role: input.role ?? current.role,
        last_seen_at: sql`(CURRENT_TIMESTAMP)`,
      })
      .where(eq(contacts.id, id))
      .returning().get();
  }
}
