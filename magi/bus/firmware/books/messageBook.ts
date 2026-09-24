import { and, count, desc, eq, lte, sql } from "drizzle-orm";
import type { BooksDb } from "../database.js";
import { messages } from "../schema.js";

export type Message = typeof messages.$inferSelect;

/** Case-insensitive substring match, the same ``instr`` test the agent tools describe. */
const matches = (query: string) => sql`instr(lower(${messages.content}), lower(${query})) > 0`;

export class MessageBook {
  constructor(private readonly db: BooksDb) {}

  add(conversationId: number, contactId: number, content: string): void {
    this.db.insert(messages).values({ conversation_id: conversationId, contact_id: contactId, content }).run();
  }

  list(conversationId: number, lastN = 20): Message[] {
    return this.db.select().from(messages)
      .where(and(eq(messages.conversation_id, conversationId), eq(messages.archived, false)))
      .orderBy(desc(messages.id))
      .limit(lastN)
      .all()
      .reverse();
  }

  count(conversationId: number): number {
    return this.db.select({ count: count() }).from(messages)
      .where(and(eq(messages.conversation_id, conversationId), eq(messages.archived, false)))
      .get()?.count ?? 0;
  }

  archiveBefore(conversationId: number, id: number): void {
    this.db.update(messages).set({ archived: true })
      .where(and(eq(messages.conversation_id, conversationId), lte(messages.id, id)))
      .run();
  }

  searchConversation(conversationId: number, query: string, limit = 20): Message[] {
    return this.db.select().from(messages)
      .where(and(eq(messages.conversation_id, conversationId), matches(query)))
      .orderBy(desc(messages.id))
      .limit(limit)
      .all();
  }

  searchContact(contactId: number, query: string, limit = 20): Message[] {
    return this.db.select().from(messages)
      .where(and(eq(messages.contact_id, contactId), matches(query)))
      .orderBy(desc(messages.id))
      .limit(limit)
      .all();
  }
}
