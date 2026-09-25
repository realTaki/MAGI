import { and, eq } from "drizzle-orm";
import { integer, sqliteTable, text, unique } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";

export const chats = sqliteTable("books_chats", {
  id: integer("id").primaryKey(),
  channel: text("channel").notNull(),
  delivery_address: text("delivery_address").notNull(),
  instruction: text("instruction").notNull().default(""),
  topic: text("topic").notNull().default(""),
  info: text("info").notNull().default(""),
  summary: text("summary").notNull().default(""),
}, (table) => [unique("books_chats_channel_address").on(table.channel, table.delivery_address)]);

export type Chat = typeof chats.$inferSelect;

export class ChatBook {
  constructor(private readonly db: BusDb) {}

  get(id: number): Chat | null {
    return this.db.select().from(chats).where(eq(chats.id, id)).get() ?? null;
  }

  forChannel(channel: string, address: string): Chat {
    this.db.insert(chats).values({ channel, delivery_address: address }).onConflictDoNothing().run();
    return this.db.select().from(chats)
      .where(and(eq(chats.channel, channel), eq(chats.delivery_address, address)))
      .get()!;
  }

  updateSummary(id: number, summary: string): void {
    this.db.update(chats).set({ summary }).where(eq(chats.id, id)).run();
  }
}
