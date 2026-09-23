import type { Database } from "bun:sqlite";

export type Message = { id: number; conversation_id: number; contact_id: number; content: string; created_at: string };

export class MessageBook {
  constructor(private readonly db: Database) {}

  add(conversationId: number, contactId: number, content: string): void {
    this.db.prepare("INSERT INTO books_messages (conversation_id, contact_id, content) VALUES (?, ?, ?)").run(conversationId, contactId, content);
  }

  list(conversationId: number, lastN = 20): Message[] {
    return (this.db.prepare("SELECT * FROM (SELECT * FROM books_messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id")
      .all(conversationId, lastN) as Message[]);
  }
}
