import type { Database } from "bun:sqlite";

export type Message = { id: number; conversation_id: number; contact_id: number; content: string; created_at: string; archived: number };

export class MessageBook {
  constructor(private readonly db: Database) {}

  add(conversationId: number, contactId: number, content: string): void {
    this.db.prepare("INSERT INTO books_messages (conversation_id, contact_id, content) VALUES (?, ?, ?)").run(conversationId, contactId, content);
  }

  list(conversationId: number, lastN = 20): Message[] {
    return (this.db.prepare("SELECT * FROM (SELECT * FROM books_messages WHERE conversation_id = ? AND archived = 0 ORDER BY id DESC LIMIT ?) ORDER BY id")
      .all(conversationId, lastN) as Message[]);
  }

  count(conversationId: number): number {
    return (this.db.prepare("SELECT COUNT(*) AS count FROM books_messages WHERE conversation_id = ? AND archived = 0").get(conversationId) as { count: number }).count;
  }

  archiveBefore(conversationId: number, id: number): void {
    this.db.prepare("UPDATE books_messages SET archived = 1 WHERE conversation_id = ? AND id <= ?").run(conversationId, id);
  }

  searchConversation(conversationId: number, query: string, limit = 20): Message[] {
    return this.db.prepare("SELECT * FROM books_messages WHERE conversation_id = ? AND instr(lower(content), lower(?)) > 0 ORDER BY id DESC LIMIT ?")
      .all(conversationId, query, limit) as Message[];
  }

  searchContact(contactId: number, query: string, limit = 20): Message[] {
    return this.db.prepare("SELECT * FROM books_messages WHERE contact_id = ? AND instr(lower(content), lower(?)) > 0 ORDER BY id DESC LIMIT ?")
      .all(contactId, query, limit) as Message[];
  }
}
