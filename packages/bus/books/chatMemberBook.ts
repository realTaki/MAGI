import { and, eq, sql } from "drizzle-orm";
import { index, integer, sqliteTable, text, unique } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";
import { contacts, type Contact } from "./contactBook.js";

/**
 * Who is in a chat. A chat is a channel address, and an address holds
 * people: the operator, this MAGI, other MAGIs in a group, guests on Telegram. Channels
 * record whoever they hear from; the contacts worker reads the result into the prompt.
 */
export const chatMembers = sqliteTable("books_chat_members", {
  id: integer("id").primaryKey(),
  chat_id: integer("chat_id").notNull(),
  contact_id: integer("contact_id").notNull().references(() => contacts.id, { onDelete: "cascade" }),
  added_at: text("added_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
}, (table) => [
  unique("books_chat_members_pair").on(table.chat_id, table.contact_id),
  index("books_chat_members_contact").on(table.contact_id),
]);

export class ChatMemberBook {
  constructor(private readonly db: BusDb) {}

  /** Seeing someone twice changes nothing: membership is a set, not a log. */
  add(chatId: number, contactId: number): void {
    this.db.insert(chatMembers)
      .values({ chat_id: chatId, contact_id: contactId })
      .onConflictDoNothing()
      .run();
  }

  remove(chatId: number, contactId: number): void {
    this.db.delete(chatMembers)
      .where(and(eq(chatMembers.chat_id, chatId), eq(chatMembers.contact_id, contactId)))
      .run();
  }

  /** The contacts in a chat, in the order they were first heard from. */
  list(chatId: number): Contact[] {
    return this.db.select({ contact: contacts }).from(chatMembers)
      .innerJoin(contacts, eq(contacts.id, chatMembers.contact_id))
      .where(eq(chatMembers.chat_id, chatId))
      .orderBy(chatMembers.id)
      .all()
      .map((row) => row.contact);
  }
}
