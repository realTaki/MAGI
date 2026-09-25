import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { and, eq, inArray, ne } from "drizzle-orm";
import Database from "better-sqlite3";
import { workspaceDatabase, migrateBooks, migrateJobs, type BusDb } from "./drizzle/database.js";
import { contacts } from "./books/contactBook.js";
import { ChatBook } from "./books/chatBook.js";
import { MessageBook } from "./books/messageBook.js";
import { MemoryBook } from "./books/memoryBook.js";
import { SkillsBook } from "./books/skillsBook.js";
import { TaskBook } from "./books/taskBook.js";
import { ContactBook } from "./books/contactBook.js";
import { ChatMemberBook } from "./books/chatMemberBook.js";
import { ChannelCursorBook } from "./books/channelCursorBook.js";
import { ContactNoteBook } from "./books/contactNoteBook.js";
import { McpServerBook } from "./books/mcpServerBook.js";
import { SettingsBook } from "./books/settingsBook.js";
import { PromptBook } from "./books/promptBook.js";
import { ToolBook } from "./books/toolBook.js";
import { JobBoard, jobs } from "./jobs/jobBoard.js";
import type { ChatNotify } from "./jobs/chatNotify.js";
import type { DeliveryNotify } from "./jobs/deliveryNotify.js";
import type { JobInput, JobType } from "./jobs/types.js";

// Contacts are numbered like everything else in the workspace: from 1. The system
// contact is the first one, the MAGI itself the second.
export const SYSTEM_CONTACT_ID = 1;
export const MAGI_CONTACT_ID = 2;

/**
 * One MAGI's shared bus: Books for durable state, Jobs for coordination.
 *
 * Workers never call each other. A component publishes a Job and whoever owns that
 * work claims it, so these two methods — `publishChat` for a turn, `publishDelivery`
 * for text that goes back out — are the only paths between them. Trouble is
 * delivered, not logged: `publishNotice` is where it goes.
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
  readonly channelCursors: ChannelCursorBook;
  readonly contactNotes: ContactNoteBook;
  readonly mcpServers: McpServerBook;
  readonly prompts: PromptBook;
  readonly settings: SettingsBook;
  readonly tools = new ToolBook();
  private readonly db: BusDb;
  private readonly logs: BusDb;
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
    mkdirSync(join(this.workspace, "logs"), { recursive: true });
    const memories = new Database(join(this.workspace, "memories", "magi.db"));
    const logs = new Database(join(this.workspace, "logs", "magi.db"));
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
    this.logs.update(jobs).set({ status: "pending", worker: null })
      .where(and(eq(jobs.status, "claimed"), ne(jobs.type, "RunToolJob"))).run();
    // A tool call belongs to the agent turn that published it, and that turn is gone
    // after a restart: answer its calls as failed instead of running them a second time.
    this.logs.update(jobs).set({ status: "failed", error: "MAGI restarted before this tool call finished" })
      .where(and(eq(jobs.type, "RunToolJob"), inArray(jobs.status, ["pending", "claimed"]))).run();
    this.settings = new SettingsBook(this.db);
    this.chats = new ChatBook(this.db);
    this.messages = new MessageBook(this.db);
    this.memoryBook = new MemoryBook(this.db);
    this.skills = new SkillsBook();
    this.tasks = new TaskBook(this.db);
    this.contacts = new ContactBook(this.db);
    this.chatMembers = new ChatMemberBook(this.db);
    this.channelCursors = new ChannelCursorBook(this.db);
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
      board = new JobBoard(this.logs, type);
      this.boards.set(type, board);
    }
    return board as JobBoard<K>;
  }

  publishChat(input: ChatNotify, publisher = "channel"): number {
    let chatId = input.chat_id;
    if (!chatId) {
      if (!input.channel?.trim() || !input.delivery_address?.trim()) throw new Error("ChatNotify needs chat_id or channel and delivery_address");
      chatId = this.chats.forChannel(input.channel.trim(), input.delivery_address.trim()).id;
    }
    if (chatId === undefined) throw new Error("chat_id is missing");
    if (!this.chats.get(chatId)) throw new Error(`chat ${chatId} does not exist`);
    const jobId = this.board("ChatNotify").publish({ ...input, chat_id: chatId }, publisher);
    this.messages.add(chatId, input.contact_id ?? SYSTEM_CONTACT_ID, input.text);
    return jobId;
  }

  publishDelivery(input: DeliveryNotify, publisher = "agent"): number {
    const chat = this.chats.get(input.chat_id);
    if (!chat) throw new Error(`chat ${input.chat_id} does not exist`);
    const jobId = this.board("DeliveryNotify").publish({ ...input, channel: chat.channel, address: chat.delivery_address }, publisher);
    this.messages.add(input.chat_id, MAGI_CONTACT_ID, input.text);
    return jobId;
  }

  /**
   * The chat the operator last spoke in: the only address a workspace has for
   * reaching them. Channels write it when they hear the operator, ``publishNotice`` reads it.
   */
  homeChat(): number | null {
    const stored = this.settings.get("home.chat_id");
    const id = stored === null ? Number.NaN : Number(stored);
    return Number.isInteger(id) && this.chats.get(id) !== null ? id : null;
  }

  setHomeChat(chatId: number): void {
    this.settings.set("home.chat_id", String(chatId));
  }

  /**
   * How a component tells the operator that something went wrong: into the chat
   * the failure belongs to when the caller knows it, otherwise into the operator's home
   * chat. Returns null before the operator has ever spoken — there is nobody to tell, and
   * that is the only case where a process-level log line is still the answer.
   */
  publishNotice(text: string, chatId?: number): number | null {
    const target = chatId ?? this.homeChat();
    if (target === null) return null;
    return this.publishDelivery({ chat_id: target, text });
  }

  close(): void {
    this.logs.$client.close();
    this.db.$client.close();
  }

}

export type { JobInput };
