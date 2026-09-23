import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { Database } from "bun:sqlite";
import { ConversationBook } from "./firmware/books/conversationBook.js";
import { MessageBook } from "./firmware/books/messageBook.js";
import { JobBoard } from "./firmware/jobs/jobBoard.js";
import type { ChatNotify, DeliveryNotify, JobInput, JobType } from "./firmware/jobs/types.js";

export const MAGI_CONTACT_ID = 1;
export const SYSTEM_CONTACT_ID = 0;

export class Bus {
  readonly workspace: string;
  readonly conversations: ConversationBook;
  readonly messages: MessageBook;
  private readonly memories: Database;
  private readonly logs: Database;
  private readonly boards = new Map<JobType, JobBoard<JobType>>();

  constructor(readonly handle: string, workspace?: string) {
    const localName = handle.replace(/^@/, "").replace(/\.magi$/, "");
    this.workspace = resolve(workspace ?? join(homedir(), ".magi", localName));
    mkdirSync(join(this.workspace, "memories"), { recursive: true });
    mkdirSync(join(this.workspace, "logs"), { recursive: true });
    this.memories = new Database(join(this.workspace, "memories", "magi.db"), { create: true });
    this.logs = new Database(join(this.workspace, "logs", "magi.db"), { create: true });
    for (const db of [this.memories, this.logs]) {
      db.exec("PRAGMA journal_mode = WAL");
      db.exec("PRAGMA busy_timeout = 5000");
    }
    this.memories.exec(`
      CREATE TABLE IF NOT EXISTS books_conversations (
        id INTEGER PRIMARY KEY, channel TEXT NOT NULL, delivery_address TEXT NOT NULL,
        instruction TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
        UNIQUE(channel, delivery_address)
      );
      CREATE TABLE IF NOT EXISTS books_messages (
        id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, contact_id INTEGER NOT NULL,
        content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX IF NOT EXISTS books_messages_conversation ON books_messages(conversation_id, id);
      CREATE TABLE IF NOT EXISTS books_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    `);
    this.logs.exec(`
      CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY, type TEXT NOT NULL, publisher TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', worker TEXT, input TEXT NOT NULL,
        output TEXT, error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(type, status, id);
    `);
    this.conversations = new ConversationBook(this.memories);
    this.messages = new MessageBook(this.memories);
  }

  board<K extends JobType>(type: K): JobBoard<K> {
    let board = this.boards.get(type);
    if (!board) {
      board = new JobBoard(this.logs, type);
      this.boards.set(type, board);
    }
    return board as JobBoard<K>;
  }

  publishChat(input: ChatNotify, publisher = "channel"): number {
    let conversationId = input.conversation_id;
    if (!conversationId) {
      if (!input.channel?.trim() || !input.delivery_address?.trim()) throw new Error("ChatNotify needs conversation_id or channel and delivery_address");
      conversationId = this.conversations.forChannel(input.channel.trim(), input.delivery_address.trim()).id;
    }
    if (!this.conversations.get(conversationId)) throw new Error(`conversation ${conversationId} does not exist`);
    const jobId = this.board("ChatNotify").publish({ ...input, conversation_id: conversationId }, publisher);
    this.messages.add(conversationId, input.contact_id ?? SYSTEM_CONTACT_ID, input.text);
    return jobId;
  }

  publishDelivery(input: DeliveryNotify, publisher = "agent"): number {
    const conversation = this.conversations.get(input.conversation_id);
    if (!conversation) throw new Error(`conversation ${input.conversation_id} does not exist`);
    const jobId = this.board("DeliveryNotify").publish({ ...input, channel: conversation.channel, address: conversation.delivery_address }, publisher);
    this.messages.add(input.conversation_id, MAGI_CONTACT_ID, input.text);
    return jobId;
  }

  getSetting(key: string): string | null {
    const row = this.memories.prepare("SELECT value FROM books_settings WHERE key = ?").get(key) as { value: string } | undefined;
    return row?.value ?? null;
  }

  setSetting(key: string, value: string): void {
    this.memories.prepare("INSERT INTO books_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").run(key, value);
  }

  close(): void {
    this.logs.close();
    this.memories.close();
  }
}

export type { JobInput };
