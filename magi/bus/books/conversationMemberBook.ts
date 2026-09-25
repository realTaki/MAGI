import { and, eq, sql } from "drizzle-orm";
import { index, integer, sqliteTable, text, unique } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";
import { contacts, type Contact } from "./contactBook.js";

/**
 * Who is in a conversation. A conversation is a channel address, and an address holds
 * people: the operator, this MAGI, other MAGIs in a group, guests on Telegram. Channels
 * record whoever they hear from, and the agent reads the result into its context.
 */
export const conversationMembers = sqliteTable("books_conversation_members", {
  id: integer("id").primaryKey(),
  conversation_id: integer("conversation_id").notNull(),
  contact_id: integer("contact_id").notNull().references(() => contacts.id, { onDelete: "cascade" }),
  added_at: text("added_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
}, (table) => [
  unique("books_conversation_members_pair").on(table.conversation_id, table.contact_id),
  index("books_conversation_members_contact").on(table.contact_id),
]);

export class ConversationMemberBook {
  constructor(private readonly db: BusDb) {}

  /** Seeing someone twice changes nothing: membership is a set, not a log. */
  add(conversationId: number, contactId: number): void {
    this.db.insert(conversationMembers)
      .values({ conversation_id: conversationId, contact_id: contactId })
      .onConflictDoNothing()
      .run();
  }

  remove(conversationId: number, contactId: number): void {
    this.db.delete(conversationMembers)
      .where(and(eq(conversationMembers.conversation_id, conversationId), eq(conversationMembers.contact_id, contactId)))
      .run();
  }

  /** The contacts in a conversation, in the order they were first heard from. */
  list(conversationId: number): Contact[] {
    return this.db.select({ contact: contacts }).from(conversationMembers)
      .innerJoin(contacts, eq(contacts.id, conversationMembers.contact_id))
      .where(eq(conversationMembers.conversation_id, conversationId))
      .orderBy(conversationMembers.id)
      .all()
      .map((row) => row.contact);
  }
}
