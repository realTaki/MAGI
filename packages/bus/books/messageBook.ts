import { and, count, desc, eq, lte, sql } from "drizzle-orm";
import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";
import { chats } from "./chatBook.js";
import { contacts } from "./contactBook.js";

export const messages = sqliteTable("books_messages", {
  id: integer("id").primaryKey(),
  chat_id: integer("chat_id").notNull().references(() => chats.id, { onDelete: "cascade" }),
  contact_id: integer("contact_id").notNull().references(() => contacts.id, { onDelete: "cascade" }),
  /** The role and content are stored exactly as the next LLM turn consumes them. */
  llm_role: text("llm_role").$type<"user" | "assistant">().notNull().default("user"),
  content: text("content").notNull(),
  created_at: text("created_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
  archived: integer("archived", { mode: "boolean" }).notNull().default(false),
}, (table) => [index("books_messages_chat").on(table.chat_id, table.id)]);

export type Message = typeof messages.$inferSelect;

/** Case-insensitive substring match, the same ``instr`` test the agent tools describe. */
const matches = (query: string) => sql`instr(lower(${messages.content}), lower(${query})) > 0`;

export class MessageBook {
  constructor(private readonly db: BusDb) {}

  add(chatId: number, contactId: number, text: string, llm_role: "user" | "assistant" = "user"): number {
    const created_at = new Date().toISOString();
    const content = llm_role === "user" ? `[contact id ${contactId} | ${created_at}]\n${text}` : text;
    return this.db.insert(messages).values({ chat_id: chatId, contact_id: contactId, llm_role, content, created_at })
      .returning({ id: messages.id })
      .get().id;
  }

  /** Look up one durable message, including archived history. */
  get(id: number): Message | undefined {
    return this.db.select().from(messages).where(eq(messages.id, id)).get();
  }

  list(chatId: number, lastN = 20): Message[] {
    return this.db.select().from(messages)
      .where(and(eq(messages.chat_id, chatId), eq(messages.archived, false)))
      .orderBy(desc(messages.id))
      .limit(lastN)
      .all()
      .reverse();
  }

  count(chatId: number): number {
    return this.db.select({ count: count() }).from(messages)
      .where(and(eq(messages.chat_id, chatId), eq(messages.archived, false)))
      .get()?.count ?? 0;
  }

  archiveBefore(chatId: number, id: number): void {
    this.db.update(messages).set({ archived: true })
      .where(and(eq(messages.chat_id, chatId), lte(messages.id, id)))
      .run();
  }

  searchChat(chatId: number, query: string, limit = 20): Message[] {
    return this.db.select().from(messages)
      .where(and(eq(messages.chat_id, chatId), matches(query)))
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
