import { and, eq } from "drizzle-orm";
import { integer, sqliteTable, text, unique } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../database.js";

export const conversations = sqliteTable("books_conversations", {
  id: integer("id").primaryKey(),
  channel: text("channel").notNull(),
  delivery_address: text("delivery_address").notNull(),
  instruction: text("instruction").notNull().default(""),
  topic: text("topic").notNull().default(""),
  info: text("info").notNull().default(""),
  summary: text("summary").notNull().default(""),
}, (table) => [unique("books_conversations_channel_address").on(table.channel, table.delivery_address)]);

export type Conversation = typeof conversations.$inferSelect;

export class ConversationBook {
  constructor(private readonly db: BusDb) {}

  get(id: number): Conversation | null {
    return this.db.select().from(conversations).where(eq(conversations.id, id)).get() ?? null;
  }

  forChannel(channel: string, address: string): Conversation {
    this.db.insert(conversations).values({ channel, delivery_address: address }).onConflictDoNothing().run();
    return this.db.select().from(conversations)
      .where(and(eq(conversations.channel, channel), eq(conversations.delivery_address, address)))
      .get()!;
  }

  updateSummary(id: number, summary: string): void {
    this.db.update(conversations).set({ summary }).where(eq(conversations.id, id)).run();
  }
}
