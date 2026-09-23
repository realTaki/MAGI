import type { Database } from "bun:sqlite";

export type Conversation = {
  id: number;
  channel: string;
  delivery_address: string;
  instruction: string;
  summary: string;
};

export class ConversationBook {
  constructor(private readonly db: Database) {}

  get(id: number): Conversation | null {
    return (this.db.prepare("SELECT * FROM books_conversations WHERE id = ?").get(id) as Conversation | undefined) ?? null;
  }

  forChannel(channel: string, address: string): Conversation {
    this.db.prepare("INSERT OR IGNORE INTO books_conversations (channel, delivery_address) VALUES (?, ?)").run(channel, address);
    return this.db.prepare("SELECT * FROM books_conversations WHERE channel = ? AND delivery_address = ?").get(channel, address) as Conversation;
  }
}
