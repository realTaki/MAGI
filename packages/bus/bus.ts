import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { and, eq, inArray, ne } from "drizzle-orm";
import Database from "better-sqlite3";
import { workspaceDatabase, migrateBooks, migrateJobs, type BusDb } from "./drizzle/database.js";
import { contacts, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID } from "./books/contactBook.js";
import { ChatBook } from "./books/chatBook.js";
import { MessageBook } from "./books/messageBook.js";
import { MemoryBook } from "./books/memoryBook.js";
import { SkillsBook } from "./books/skillsBook.js";
import { TaskBook } from "./books/taskBook.js";
import { ContactBook } from "./books/contactBook.js";
import { ChatMemberBook } from "./books/chatMemberBook.js";
import { ContactNoteBook } from "./books/contactNoteBook.js";
import { McpServerBook } from "./books/mcpServerBook.js";
import { SettingsBook } from "./books/settingsBook.js";
import { PromptBook } from "./books/promptBook.js";
import { ToolBook } from "./books/toolBook.js";
import { JobBoard, jobs, type JobInput, type JobType } from "./jobs/jobBoard.js";

/**
 * One MAGI's shared bus: Books for durable state, Jobs for coordination.
 *
 * Workers never call each other. A component publishes a Job and whoever owns that
 * work claims it; what a message job means for the workspace — which row it records,
 * where its text is read from — lives with the job itself in ``jobs/``, so nothing
 * here has to know the message jobs one by one. Trouble is delivered, not logged,
 * through `messageDelivery.notify`.
 */
export class Bus {
  readonly workspace: string;
  readonly chats: ChatBook;
  readonly messages: MessageBook;
  readonly memoryBook: MemoryBook;
  readonly skills: SkillsBook;
  readonly tasks: TaskBook;
  readonly contacts: ContactBook;
  readonly chatMembers: ChatMemberBook;
  readonly contactNotes: ContactNoteBook;
  readonly mcpServers: McpServerBook;
  readonly prompts: PromptBook;
  readonly settings: SettingsBook;
  readonly tools = new ToolBook();
  private readonly db: BusDb;
  private readonly jobs: BusDb;
  private readonly boards = new Map<JobType, JobBoard<JobType>>();

  constructor(readonly handle: string, workspace?: string) {
    const localName = handle.replace(/^@/, "").replace(/\.magi$/, "");
    // A MAGI workspace is a sibling of app/, asp/, and the MAGI checkout.
    // Do not put it under ".magi/magi": macOS normally has a case-insensitive
    // filesystem, so that path aliases the ".magi/MAGI" source checkout.
    const current = join(homedir(), ".magi", localName);
    // Workspaces that an earlier release created under ".magi/ts-magi/<name>" are
    // opened where they are: agents already have data there.
    const previous = join(homedir(), ".magi", "ts-magi", localName);
    this.workspace = resolve(
      workspace ?? (existsSync(current) || !existsSync(previous) ? current : previous),
    );
    mkdirSync(join(this.workspace, "memories"), { recursive: true });
    mkdirSync(join(this.workspace, "jobs"), { recursive: true });
    const memories = new Database(join(this.workspace, "memories", "magi.db"));
    const jobDb = new Database(join(this.workspace, "jobs", "magi.db"));
    for (const client of [memories, jobDb]) {
      client.exec("PRAGMA journal_mode = WAL");
      client.exec("PRAGMA busy_timeout = 5000");
    }
    // Older migrations rebuild parent tables. Disable checks while they run even if a
    // caller supplied a connection with checks already enabled, then enforce every
    // declared relationship for ordinary Book operations.
    memories.pragma("foreign_keys = OFF");
    // Schema history lives in ``bus/drizzle/``, one folder per database.
    this.db = workspaceDatabase(memories);
    migrateBooks(this.db);
    // SQLite recognizes foreign-key declarations only when each connection enables them.
    // Migrations run first because older migrations rebuild parent tables.
    memories.pragma("foreign_keys = ON");
    this.jobs = workspaceDatabase(jobDb);
    migrateJobs(this.jobs);
    // One MAGI owns this workspace. Recover work interrupted by a process exit.
    this.jobs.update(jobs).set({ status: "pending", worker: null })
      .where(and(eq(jobs.status, "claimed"), ne(jobs.type, "RunToolJob"))).run();
    // A tool call belongs to the agent turn that published it, and that turn is gone
    // after a restart: answer its calls as failed instead of running them a second time.
    this.jobs.update(jobs).set({ status: "failed", error: "MAGI restarted before this tool call finished" })
      .where(and(eq(jobs.type, "RunToolJob"), inArray(jobs.status, ["pending", "claimed"]))).run();
    this.settings = new SettingsBook(this.db);
    this.chats = new ChatBook(this.db);
    this.messages = new MessageBook(this.db);
    this.memoryBook = new MemoryBook(this.db);
    this.skills = new SkillsBook();
    this.tasks = new TaskBook(this.db);
    this.contacts = new ContactBook(this.db);
    this.chatMembers = new ChatMemberBook(this.db);
    this.contactNotes = new ContactNoteBook(this.db);
    this.mcpServers = new McpServerBook(this.db);
    this.prompts = new PromptBook(this.workspace);
    // The system contact stands for the operator: "@user.magi" is the handle ASP gives them,
    // so their messages arrive already known. The MAGI is the other fixed contact, and
    // both record the identity they speak with — anyone else becomes a contact of their
    // own the first time they are heard from.
    this.db.insert(contacts).values({ id: SYSTEM_CONTACT_ID, name: "system", role: "system", asp_handle: "@user.magi" })
      .onConflictDoUpdate({ target: contacts.id, set: { asp_handle: "@user.magi" } })
      .run();
    this.db.insert(contacts).values({ id: MAGI_CONTACT_ID, name: handle, role: "magi", asp_handle: handle })
      .onConflictDoUpdate({ target: contacts.id, set: { name: handle, asp_handle: handle } })
      .run();
  }

  board<K extends JobType>(type: K): JobBoard<K> {
    let board = this.boards.get(type);
    if (!board) {
      board = new JobBoard(this.jobs, type);
      this.boards.set(type, board);
    }
    return board as JobBoard<K>;
  }

  close(): void {
    this.jobs.$client.close();
    this.db.$client.close();
  }

}

export type { JobInput };
