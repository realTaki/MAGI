import { cpSync, existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { eq } from "drizzle-orm";
import { Database, type SQLQueryBindings } from "bun:sqlite";
import { workspaceDatabase, migrateBooks, migrateJobs, type BusDb } from "./firmware/database.js";
import { contacts } from "./firmware/books/contactBook.js";
import { ConversationBook } from "./firmware/books/conversationBook.js";
import { MessageBook } from "./firmware/books/messageBook.js";
import { MemoryBook } from "./firmware/books/memoryBook.js";
import { SkillsBook } from "./firmware/books/skillsBook.js";
import { TaskBook } from "./firmware/books/taskBook.js";
import { ContactBook } from "./firmware/books/contactBook.js";
import { ContactNoteBook } from "./firmware/books/contactNoteBook.js";
import { McpServerBook } from "./firmware/books/mcpServerBook.js";
import { SettingsBook } from "./firmware/books/settingsBook.js";
import { PromptBook } from "./firmware/books/promptBook.js";
import { ToolBook } from "./firmware/books/toolBook.js";
import { JobBoard, jobs } from "./firmware/jobs/jobBoard.js";
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
  readonly settings: SettingsBook;
  readonly tools = new ToolBook();
  private readonly db: BusDb;
  private readonly logs: BusDb;
  private readonly boards = new Map<JobType, JobBoard<JobType>>();

  constructor(readonly handle: string, workspace?: string, migrationSource?: string | null) {
    const localName = handle.replace(/^@/, "").replace(/\.magi$/, "");
    // A MAGI workspace is a sibling of app/, asp/, and the MAGI checkout.
    // Do not put it under ".magi/magi": macOS normally has a case-insensitive
    // filesystem, so that path aliases the ".magi/MAGI" source checkout.
    const current = join(homedir(), ".magi", localName);
    const previous = join(homedir(), ".magi", "ts-magi", localName);
    this.workspace = resolve(
      workspace ?? (existsSync(current) || !existsSync(previous) ? current : previous),
    );
    // When using the current location it is already this TypeScript workspace,
    // not a Python source to import from. Only the legacy ts-magi fallback
    // imports from the former root-level Python workspace.
    const pythonWorkspace = migrationSource === undefined
      ? (workspace === undefined && this.workspace !== resolve(current) ? current : null)
      : migrationSource;
    mkdirSync(join(this.workspace, "memories"), { recursive: true });
    mkdirSync(join(this.workspace, "logs"), { recursive: true });
    const memories = new Database(join(this.workspace, "memories", "magi.db"), { create: true });
    const logs = new Database(join(this.workspace, "logs", "magi.db"), { create: true });
    const settingsColumns = memories.query("PRAGMA table_info(books_settings)").all() as Array<{ name: string }>;
    if (settingsColumns.some((column) => column.name === "id")) {
      logs.close();
      memories.close();
      throw new Error("this workspace uses py-magi's SQLite schema; choose a separate workspace");
    }
    for (const client of [memories, logs]) {
      client.exec("PRAGMA journal_mode = WAL");
      client.exec("PRAGMA busy_timeout = 5000");
    }
    // Schema history lives in ``bus/drizzle/``, one folder per database.
    this.db = workspaceDatabase(memories);
    migrateBooks(this.db);
    this.logs = workspaceDatabase(logs);
    migrateJobs(this.logs);
    // One MAGI owns this workspace. Recover work interrupted by a process exit.
    this.logs.update(jobs).set({ status: "pending", worker: null }).where(eq(jobs.status, "claimed")).run();
    // The migration below reads and marks settings, so this one is built first.
    this.settings = new SettingsBook(this.db);
    if (pythonWorkspace) this.migratePythonWorkspace(pythonWorkspace);
    this.conversations = new ConversationBook(this.db);
    this.messages = new MessageBook(this.db);
    this.memoryBook = new MemoryBook(this.db);
    this.skills = new SkillsBook(this.workspace);
    this.tasks = new TaskBook(this.db);
    this.contacts = new ContactBook(this.db);
    this.contactNotes = new ContactNoteBook(this.db);
    this.mcpServers = new McpServerBook(this.db);
    this.prompts = new PromptBook(this.workspace);
    this.db.insert(contacts).values({ id: SYSTEM_CONTACT_ID, name: "system", role: "system" }).onConflictDoNothing().run();
    this.db.insert(contacts).values({ id: MAGI_CONTACT_ID, name: handle, role: "magi" })
      .onConflictDoUpdate({ target: contacts.id, set: { name: handle } })
      .run();
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

  close(): void {
    this.logs.$client.close();
    this.db.$client.close();
  }

  private migratePythonWorkspace(sourceWorkspace: string): void {
    const marker = this.settings.get("migration.py_magi");
    const sourcePath = join(sourceWorkspace, "memories", "magi.db");
    if (marker || !existsSync(sourcePath)) return;
    const source = new Database(sourcePath, { readonly: true });
    try {
      const tables = new Set((source.query("SELECT name FROM sqlite_master WHERE type = 'table'").all() as Array<{ name: string }>).map((row) => row.name));
      if (!tables.has("books_settings")) return;
      this.db.$client.transaction(() => {
        for (const row of source.query("SELECT id, channel, delivery_address, instruction, topic, info, summary FROM books_conversations").all() as Array<Record<string, unknown>>) {
          this.db.$client.prepare("INSERT OR IGNORE INTO books_conversations (id, channel, delivery_address, instruction, topic, info, summary) VALUES (?, ?, ?, ?, ?, ?, ?)")
            .run(...[row.id, row.channel, row.delivery_address, row.instruction ?? "", row.topic ?? "", row.info ?? "", row.summary ?? ""].map(sqlValue));
        }
        for (const row of source.query("SELECT id, conversation_id, contact_id, content, timestamp, archived FROM books_messages").all() as Array<Record<string, unknown>>) {
          this.db.$client.prepare("INSERT OR IGNORE INTO books_messages (id, conversation_id, contact_id, content, created_at, archived) VALUES (?, ?, ?, ?, ?, ?)")
            .run(...[row.id, row.conversation_id, row.contact_id, row.content, row.timestamp, row.archived ? 1 : 0].map(sqlValue));
        }
        copyRows(source, this.db.$client, "books_contacts", ["id", "name", "nickname", "role", "last_seen_at"]);
        copyRows(source, this.db.$client, "books_contact_notes", ["id", "contact_id", "note", "kind", "created_at"]);
        copyRows(source, this.db.$client, "books_memories", ["id", "topic", "detail", "kind", "archived", "created_at"]);
        for (const { key, value } of new SettingsBook(workspaceDatabase(source)).all()) this.settings.set(key, value);
        for (const row of source.query("SELECT id, name, prompt, source, enabled, cron, conversation_id FROM books_tasks").all() as Array<Record<string, unknown>>) {
          this.db.$client.prepare("INSERT OR IGNORE INTO books_tasks (id, name, prompt, source, enabled, cron, conversation_id) VALUES (?, ?, ?, ?, ?, ?, ?)")
            .run(...[row.id, row.name, row.prompt, row.source, row.enabled ? 1 : 0, row.cron, row.conversation_id].map(sqlValue));
        }
        this.settings.set("migration.py_magi", new Date().toISOString());
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
