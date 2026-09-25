import { eq, sql } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";

export const contacts = sqliteTable("books_contacts", {
  id: integer("id").primaryKey(),
  name: text("name").notNull().unique(),
  nickname: text("nickname"),
  role: text("role").$type<"system" | "authorized" | "stranger" | "magi" | "third_party_agent">().notNull().default("stranger"),
  // A chat is a channel address, and an address can hold several people — a
  // group chat, a Telegram group. Who spoke is therefore a contact, and the identity
  // they spoke with belongs to that contact: one per channel, learned from the message.
  tg_id: text("tg_id").unique(),
  asp_handle: text("asp_handle").unique(),
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

  /** The contact behind an ASP handle, created the first time they are heard from. */
  forAspHandle(handle: string): Contact {
    const clean = handle.trim();
    const known = this.db.select().from(contacts).where(eq(contacts.asp_handle, clean)).get();
    if (known) return this.seen(known);
    // A workspace from before identities were recorded has the contact already, under
    // the name the handle carries: remember the identity instead of making a second row.
    const named = this.db.select().from(contacts).where(eq(contacts.name, clean)).get();
    if (named) return this.db.update(contacts).set({ asp_handle: clean }).where(eq(contacts.id, named.id)).returning().get();
    return this.db.insert(contacts)
      .values({ name: clean, asp_handle: clean, role: clean.endsWith(".magi") ? "magi" : "stranger" })
      .returning().get();
  }

  /** The contact behind a Telegram user id, created the first time they are heard from. */
  forTg(tgId: string): Contact {
    const clean = tgId.trim();
    const known = this.db.select().from(contacts).where(eq(contacts.tg_id, clean)).get();
    if (known) return this.seen(known);
    const named = this.db.select().from(contacts).where(eq(contacts.name, `tg:${clean}`)).get();
    if (named) return this.db.update(contacts).set({ tg_id: clean }).where(eq(contacts.id, named.id)).returning().get();
    return this.db.insert(contacts).values({ name: `tg:${clean}`, tg_id: clean }).returning().get();
  }

  create(input: { name: string; nickname?: string; role?: ContactRole; tg_id?: string; asp_handle?: string }): Contact {
    if (!input.name.trim()) throw new Error("contact name is required");
    try {
      return this.db.insert(contacts)
        .values({
          name: input.name.trim(),
          nickname: input.nickname?.trim() || null,
          role: input.role ?? "stranger",
          tg_id: input.tg_id?.trim() || null,
          asp_handle: input.asp_handle?.trim() || null,
        })
        .returning().get();
    } catch (error) {
      if (String(error).includes("UNIQUE")) throw new Error(`contact ${input.name.trim()} already exists`);
      throw error;
    }
  }

  update(id: number, input: { name?: string; nickname?: string | null; role?: ContactRole; tg_id?: string | null; asp_handle?: string | null }): Contact {
    const current = this.get(id);
    if (!current) throw new Error(`contact ${id} not found`);
    return this.db.update(contacts)
      .set({
        name: input.name?.trim() || current.name,
        nickname: input.nickname === undefined ? current.nickname : input.nickname?.trim() || null,
        role: input.role ?? current.role,
        tg_id: input.tg_id === undefined ? current.tg_id : input.tg_id?.trim() || null,
        asp_handle: input.asp_handle === undefined ? current.asp_handle : input.asp_handle?.trim() || null,
        last_seen_at: sql`(CURRENT_TIMESTAMP)`,
      })
      .where(eq(contacts.id, id))
      .returning().get();
  }

  private seen(contact: Contact): Contact {
    this.db.update(contacts).set({ last_seen_at: sql`(CURRENT_TIMESTAMP)` }).where(eq(contacts.id, contact.id)).run();
    return contact;
  }
}
