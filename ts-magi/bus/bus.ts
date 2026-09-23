import { cpSync, existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { Database, type SQLQueryBindings } from "bun:sqlite";
import { ConversationBook } from "./firmware/books/conversationBook.js";
import { MessageBook } from "./firmware/books/messageBook.js";
import { MemoryBook } from "./firmware/books/memoryBook.js";
import { SkillsBook } from "./firmware/books/skillsBook.js";
import { TaskBook } from "./firmware/books/taskBook.js";
import { ContactBook } from "./firmware/books/contactBook.js";
import { ContactNoteBook } from "./firmware/books/contactNoteBook.js";
import { McpServerBook } from "./firmware/books/mcpServerBook.js";
import { PromptBook } from "./firmware/books/promptBook.js";
import { JobBoard } from "./firmware/jobs/jobBoard.js";
import type { ChatNotify, DeliveryNotify, JobInput, JobType } from "./firmware/jobs/types.js";

export const MAGI_CONTACT_ID = 1;
export const SYSTEM_CONTACT_ID = 0;

export class Bus {
  readonly workspace: string;
  readonly conversations: ConversationBook;
  readonly messages: MessageBook;
  readonly memoryBook: MemoryBook;
  readonly skills: SkillsBook;
  readonly tasks: TaskBook;
  readonly contacts: ContactBook;
  readonly contactNotes: ContactNoteBook;
  readonly mcpServers: McpServerBook;
  readonly prompts: PromptBook;
  private readonly memories: Database;
  private readonly logs: Database;
  private readonly boards = new Map<JobType, JobBoard<JobType>>();

  constructor(readonly handle: string, workspace?: string, migrationSource?: string | null) {
    const localName = handle.replace(/^@/, "").replace(/\.magi$/, "");
    this.workspace = resolve(workspace ?? join(homedir(), ".magi", "ts-magi", localName));
    const pythonWorkspace = migrationSource === undefined ? (workspace === undefined ? join(homedir(), ".magi", localName) : null) : migrationSource;
    mkdirSync(join(this.workspace, "memories"), { recursive: true });
    mkdirSync(join(this.workspace, "logs"), { recursive: true });
    this.memories = new Database(join(this.workspace, "memories", "magi.db"), { create: true });
    this.logs = new Database(join(this.workspace, "logs", "magi.db"), { create: true });
    const settingsColumns = this.memories.query("PRAGMA table_info(books_settings)").all() as Array<{ name: string }>;
    if (settingsColumns.some((column) => column.name === "id")) {
      this.logs.close();
      this.memories.close();
      throw new Error("this workspace uses py-magi's SQLite schema; choose a separate MAGI_WORKSPACE");
    }
    for (const db of [this.memories, this.logs]) {
      db.exec("PRAGMA journal_mode = WAL");
      db.exec("PRAGMA busy_timeout = 5000");
    }
    this.memories.exec(`
      CREATE TABLE IF NOT EXISTS books_conversations (
        id INTEGER PRIMARY KEY, channel TEXT NOT NULL, delivery_address TEXT NOT NULL,
        instruction TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '', info TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
        UNIQUE(channel, delivery_address)
      );
      CREATE TABLE IF NOT EXISTS books_messages (
        id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, contact_id INTEGER NOT NULL,
        content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        archived INTEGER NOT NULL DEFAULT 0
      );
      CREATE INDEX IF NOT EXISTS books_messages_conversation ON books_messages(conversation_id, id);
      CREATE TABLE IF NOT EXISTS books_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS books_memories (
        id INTEGER PRIMARY KEY, topic TEXT NOT NULL, detail TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'temporary', archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE IF NOT EXISTS books_tasks (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, prompt TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'user', enabled INTEGER NOT NULL DEFAULT 1,
        cron TEXT NOT NULL, conversation_id INTEGER NOT NULL,
        last_fired_minute TEXT
      );
      CREATE TABLE IF NOT EXISTS books_contacts (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, nickname TEXT,
        role TEXT NOT NULL DEFAULT 'stranger', last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE IF NOT EXISTS books_contact_notes (
        id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL REFERENCES books_contacts(id) ON DELETE CASCADE,
        note TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'permanent', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE IF NOT EXISTS books_mcp_servers (
        name TEXT PRIMARY KEY, connection_type TEXT NOT NULL, command TEXT, args TEXT NOT NULL DEFAULT '[]', url TEXT,
        env TEXT NOT NULL DEFAULT '{}', headers TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
        connect_timeout REAL, execute_timeout REAL, sse_read_timeout REAL
      );
    `);
    const messageColumns = this.memories.query("PRAGMA table_info(books_messages)").all() as Array<{ name: string }>;
    if (!messageColumns.some((column) => column.name === "archived")) this.memories.exec("ALTER TABLE books_messages ADD COLUMN archived INTEGER NOT NULL DEFAULT 0");
    const conversationColumns = this.memories.query("PRAGMA table_info(books_conversations)").all() as Array<{ name: string }>;
    if (!conversationColumns.some((column) => column.name === "topic")) this.memories.exec("ALTER TABLE books_conversations ADD COLUMN topic TEXT NOT NULL DEFAULT ''");
    if (!conversationColumns.some((column) => column.name === "info")) this.memories.exec("ALTER TABLE books_conversations ADD COLUMN info TEXT NOT NULL DEFAULT ''");
    this.logs.exec(`
      CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY, type TEXT NOT NULL, publisher TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', worker TEXT, input TEXT NOT NULL,
        output TEXT, error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(type, status, id);
    `);
    // One MAGI owns this workspace. Recover work interrupted by a process exit.
    this.logs.exec("UPDATE jobs SET status = 'pending', worker = NULL WHERE status = 'claimed'");
    if (pythonWorkspace) this.migratePythonWorkspace(pythonWorkspace);
    this.conversations = new ConversationBook(this.memories);
    this.messages = new MessageBook(this.memories);
    this.memoryBook = new MemoryBook(this.memories);
    this.skills = new SkillsBook(this.workspace);
    this.tasks = new TaskBook(this.memories);
    this.contacts = new ContactBook(this.memories);
    this.contactNotes = new ContactNoteBook(this.memories);
    this.mcpServers = new McpServerBook(this.memories);
    this.prompts = new PromptBook(this.workspace);
    this.memories.prepare("INSERT OR IGNORE INTO books_contacts (id, name, role) VALUES (0, 'system', 'system')").run();
    this.memories.prepare("INSERT INTO books_contacts (id, name, role) VALUES (1, ?, 'magi') ON CONFLICT(id) DO UPDATE SET name = excluded.name").run(handle);
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

  private migratePythonWorkspace(sourceWorkspace: string): void {
    const marker = this.getSetting("migration.py_magi");
    const sourcePath = join(sourceWorkspace, "memories", "magi.db");
    if (marker || !existsSync(sourcePath)) return;
    const source = new Database(sourcePath, { readonly: true });
    try {
      const tables = new Set((source.query("SELECT name FROM sqlite_master WHERE type = 'table'").all() as Array<{ name: string }>).map((row) => row.name));
      if (!tables.has("books_settings")) return;
      this.memories.transaction(() => {
        for (const row of source.query("SELECT id, channel, delivery_address, instruction, topic, info, summary FROM books_conversations").all() as Array<Record<string, unknown>>) {
          this.memories.prepare("INSERT OR IGNORE INTO books_conversations (id, channel, delivery_address, instruction, topic, info, summary) VALUES (?, ?, ?, ?, ?, ?, ?)")
            .run(...[row.id, row.channel, row.delivery_address, row.instruction ?? "", row.topic ?? "", row.info ?? "", row.summary ?? ""].map(sqlValue));
        }
        for (const row of source.query("SELECT id, conversation_id, contact_id, content, timestamp, archived FROM books_messages").all() as Array<Record<string, unknown>>) {
          this.memories.prepare("INSERT OR IGNORE INTO books_messages (id, conversation_id, contact_id, content, created_at, archived) VALUES (?, ?, ?, ?, ?, ?)")
            .run(...[row.id, row.conversation_id, row.contact_id, row.content, row.timestamp, row.archived ? 1 : 0].map(sqlValue));
        }
        copyRows(source, this.memories, "books_contacts", ["id", "name", "nickname", "role", "last_seen_at"]);
        copyRows(source, this.memories, "books_contact_notes", ["id", "contact_id", "note", "kind", "created_at"]);
        copyRows(source, this.memories, "books_memories", ["id", "topic", "detail", "kind", "archived", "created_at"]);
        for (const row of source.query("SELECT key, value FROM books_settings").all() as Array<{ key: string; value: string }>) this.setSetting(row.key, row.value);
        for (const row of source.query("SELECT id, name, prompt, source, enabled, cron, conversation_id FROM books_tasks").all() as Array<Record<string, unknown>>) {
          this.memories.prepare("INSERT OR IGNORE INTO books_tasks (id, name, prompt, source, enabled, cron, conversation_id) VALUES (?, ?, ?, ?, ?, ?, ?)")
            .run(...[row.id, row.name, row.prompt, row.source, row.enabled ? 1 : 0, row.cron, row.conversation_id].map(sqlValue));
        }
        this.setSetting("migration.py_magi", new Date().toISOString());
      }).immediate();
      for (const relative of ["prompts", "skills"]) {
        const from = join(sourceWorkspace, relative);
        const to = join(this.workspace, relative);
        if (existsSync(from) && !existsSync(to)) cpSync(from, to, { recursive: true });
      }
    } finally { source.close(); }
  }
}

function copyRows(source: Database, target: Database, table: string, columns: string[]): void {
  const placeholders = columns.map(() => "?").join(", ");
  const insert = target.prepare(`INSERT OR IGNORE INTO ${table} (${columns.join(", ")}) VALUES (${placeholders})`);
  for (const row of source.query(`SELECT ${columns.join(", ")} FROM ${table}`).all() as Array<Record<string, unknown>>) insert.run(...columns.map((column) => sqlValue(row[column])));
}

function sqlValue(value: unknown): SQLQueryBindings {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "bigint" || typeof value === "boolean" || ArrayBuffer.isView(value)) return value as SQLQueryBindings;
  throw new Error(`unsupported SQLite migration value: ${typeof value}`);
}

export type { JobInput };
