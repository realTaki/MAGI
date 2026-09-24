import { and, eq } from "drizzle-orm";
import type { BooksDb } from "../database.js";
import { conversations } from "../schema.js";

export type Conversation = typeof conversations.$inferSelect;

export class ConversationBook {
  constructor(private readonly db: BooksDb) {}

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
