import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { magiHome } from "@magi/launcher/runtime/home.js";
import { and, eq, inArray, ne, or } from "drizzle-orm";
import { openDatabase, pragma, transaction } from "./sqlite.js";
import { workspaceDatabase, migrateBooks, migrateJobs, type BusDb } from "./drizzle/database.js";
import { contacts, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID } from "./books/contactBook.js";
import { GroupBook } from "./books/groupBook.js";
import { DeliveryBook } from "./books/deliveryBook.js";
import { ChatBook } from "./books/chatBook.js";
import { MessageBook } from "./books/messageBook.js";
import { FeedBook } from "./books/feedBook.js";
import { ActionItemBook } from "./books/actionItemBook.js";
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
import { agentTurnsCache, AgentTurnCache } from "./jobs/agentTurnCache.js";
import { USER_HANDLE, defaultDisplayName } from "./identity.js";
import { loadOrCreateIdentity, type Identity } from "./identityKeys.js";
import { SecretStore } from "./secrets.js";
import { messageDelivery } from "./jobs/messageDelivery.js";
import { CARD_RESTART_ERROR } from "./jobs/card.js";
import { replaceFailedPath } from "./replaceMarker.js";

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
  readonly deliveries: DeliveryBook;
  readonly groups: GroupBook;
  readonly messages: MessageBook;
  readonly memoryBook: MemoryBook;
  readonly actionItems: ActionItemBook;
  readonly feed: FeedBook;
  readonly skills: SkillsBook;
  readonly tasks: TaskBook;
  readonly contacts: ContactBook;
  readonly userContactId: number;
  readonly chatMembers: ChatMemberBook;
  readonly contactNotes: ContactNoteBook;
  readonly mcpServers: McpServerBook;
  readonly prompts: PromptBook;
  readonly settings: SettingsBook;
  readonly secrets: SecretStore;
  readonly identity: Identity;
  readonly tools = new ToolBook();
  readonly agentTurns: AgentTurnCache;
  private readonly db: BusDb;
  private readonly jobs: BusDb;
  private readonly boards = new Map<JobType, JobBoard<JobType>>();

  constructor(readonly handle: string, workspace?: string) {
    const localName = handle.replace(/^@/, "").replace(/\.magi$/, "");
    // Each EVA workspace is independent of other node workspaces.
    // Do not put it under ".magi/magi": macOS normally has a case-insensitive
    // filesystem, so that path aliases a ".magi/MAGI" source checkout.
    this.workspace = resolve(workspace ?? join(magiHome(), localName));
    const memoriesPath = join(this.workspace, "memories", "books.db");
    const jobsPath = join(this.workspace, "logs", "jobs.db");
    mkdirSync(join(this.workspace, "memories"), { recursive: true });
    mkdirSync(join(this.workspace, "logs"), { recursive: true });
    const memories = openDatabase(memoriesPath);
    const jobDb = openDatabase(jobsPath);
    for (const client of [memories, jobDb]) {
      client.exec("PRAGMA journal_mode = WAL");
      client.exec("PRAGMA busy_timeout = 5000");
    }
    // Older migrations rebuild parent tables. Disable checks while they run even if a
    // caller supplied a connection with checks already enabled, then enforce every
    // declared relationship for ordinary Book operations.
    pragma(memories, "foreign_keys = OFF");
    // Schema history lives in ``bus/drizzle/``, one folder per database.
    this.db = workspaceDatabase(memories);
    migrateBooks(this.db);
    // SQLite recognizes foreign-key declarations only when each connection enables them.
    // Migrations run first because older migrations rebuild parent tables.
    pragma(memories, "foreign_keys = ON");
    this.jobs = workspaceDatabase(jobDb);
    migrateJobs(this.jobs);
    pragma(jobDb, "foreign_keys = ON");
    // A marker names the replace the shell could not restore. Leave that claimed
    // job alone so this start can record the marker's error on it. A marker with
    // no job id fails every open replace instead.
    const replaceMarker = replaceFailedPath(handle);
    let markedJob: number | null = null;
    let markerError: string | null = null;
    if (existsSync(replaceMarker)) {
      try {
        const body = JSON.parse(readFileSync(replaceMarker, "utf8")) as { job?: unknown; error?: unknown };
        if (typeof body.job === "number" && Number.isInteger(body.job)) markedJob = body.job;
        if (typeof body.error === "string" && body.error !== "") markerError = body.error;
      } catch {
        markedJob = null;
      }
    }
    const handoff = Boolean(process.env.MAGI_REPLACE_JOB || process.env.MAGI_REPLACE_ERROR);
    // One MAGI owns this workspace. Recover work interrupted by a process exit.
    // One process runs one replace, so a marker names the only claimed ReplaceSelfJob.
    // Claims without a turn journal never began execution (for example, queued
    // same-chat deliveries from an older build); leave those eligible for requeue.
    const interruptedTurns = this.jobs.select().from(jobs).where(and(eq(jobs.type, "MessageDeliveryJob"), eq(jobs.status, "claimed"), inArray(jobs.worker, ["agent", "subconscious"]))).all()
      .filter((turn) => this.jobs.select({ id: agentTurnsCache.id }).from(agentTurnsCache).where(eq(agentTurnsCache.turn_id, turn.id)).limit(1).get());
    for (const turn of interruptedTurns) this.jobs.update(jobs).set({ status: "failed", error: "MAGI restarted; this turn was interrupted and was not retried" }).where(eq(jobs.id, turn.id)).run();
    this.jobs.update(jobs).set({ status: "failed", error: "MAGI restarted before this model call finished" })
      .where(and(eq(jobs.type, "CallLLMJob"), inArray(jobs.status, ["pending", "claimed"]))).run();
    const open = and(eq(jobs.status, "claimed"), ne(jobs.type, "RunToolJob"));
    this.jobs.update(jobs).set({ status: "pending", worker: null })
      .where(markedJob !== null && !handoff
        ? and(open, or(ne(jobs.type, "ReplaceSelfJob"), ne(jobs.id, markedJob)))
        : open)
      .run();
    // A tool call belongs to the agent turn that published it, and that turn is gone
    // after a restart: answer its calls as failed instead of running them a second time.
    this.jobs.update(jobs).set({ status: "failed", error: "MAGI restarted before this tool call finished" })
      .where(and(eq(jobs.type, "RunToolJob"), inArray(jobs.status, ["pending", "claimed"]))).run();
    this.jobs.update(jobs).set({ status: "failed", error: CARD_RESTART_ERROR })
      .where(and(eq(jobs.type, "CardJob"), inArray(jobs.status, ["pending", "claimed"]))).run();
    this.jobs.update(jobs).set({ status: "failed", error: "MAGI restarted before this config was written" })
      .where(and(eq(jobs.type, "ConfigureJob"), inArray(jobs.status, ["pending", "claimed"]))).run();
    // A supervised replace hands the job to the new process, which completes it
    // after its own workers are healthy. Any other restart failed the swap.
    if (!handoff && markedJob === null) {
      this.jobs.update(jobs).set({ status: "failed", error: markerError ?? "MAGI restarted before this replace finished" })
        .where(and(eq(jobs.type, "ReplaceSelfJob"), inArray(jobs.status, ["pending", "claimed"]))).run();
    }
    this.jobs.update(jobs).set({ status: "failed", error: "MAGI restarted before this secret was applied" })
      .where(and(eq(jobs.type, "InboundSecretJob"), inArray(jobs.status, ["pending", "claimed"]))).run();
    this.settings = new SettingsBook(this.db);
    this.secrets = SecretStore.open(this.workspace);
    this.identity = loadOrCreateIdentity(this.workspace);
    this.chats = new ChatBook(this.db);
    this.deliveries = new DeliveryBook(this.db);
    this.groups = new GroupBook(this.db, this);
    this.messages = new MessageBook(this.db);
    this.memoryBook = new MemoryBook(this.db);
    this.actionItems = new ActionItemBook(this.db);
    this.feed = new FeedBook(this.db);
    this.skills = new SkillsBook();
    this.tasks = new TaskBook(this.db);
    this.contacts = new ContactBook(this.db);
    this.chatMembers = new ChatMemberBook(this.db);
    this.contactNotes = new ContactNoteBook(this.db);
    this.mcpServers = new McpServerBook(this.db);
    this.prompts = new PromptBook(this.workspace);
    this.agentTurns = new AgentTurnCache(this.jobs);
    // System, self and the human are separate persistent identities. Never reserve
    // ID 3: historical workspaces may already have a peer there.
    this.db.insert(contacts).values({ id: SYSTEM_CONTACT_ID, name: "system", role: "system" })
      .onConflictDoUpdate({ target: contacts.id, set: { handle: null, role: "system" } }).run();
    this.db.insert(contacts).values({ id: MAGI_CONTACT_ID, name: defaultDisplayName(handle), role: "magi", handle })
      .onConflictDoUpdate({ target: contacts.id, set: { name: defaultDisplayName(handle), handle } }).run();
    this.userContactId = transaction(this.db.$client, () => {
      const stored = Number(this.settings.get("identity.user_contact_id"));
      if (stored > MAGI_CONTACT_ID && this.contacts.get(stored)) return stored;
      let name = "user";
      for (let suffix = 1; this.contacts.list().some(contact => contact.name === name); suffix++) name = `user:${suffix}`;
      const user = this.contacts.create({ name, role: "authorized", handle: this.contacts.list().some(contact => contact.handle === USER_HANDLE) ? undefined : USER_HANDLE });
      this.settings.set("identity.user_contact_id", String(user.id));
      // Only journal-backed local human ingress is attributable. Older unjournaled
      // rows stay system/unknown rather than inventing an author from their text.
      this.db.$client.prepare(`UPDATE books_messages SET contact_id = ? WHERE contact_id = ? AND id IN (
        SELECT d.message_id FROM books_deliveries d JOIN books_messages m ON m.id = d.message_id
        JOIN books_chats c ON c.id = m.chat_id WHERE
        (d.publisher = 'cli' AND c.channel = 'cli') OR
        (d.publisher = 'local-owner' AND c.channel = 'local-owner') OR
        (d.publisher = 'webapp' AND c.channel = 'shell-owner'))`).run(user.id, SYSTEM_CONTACT_ID);
      return user.id;
    }, "immediate");
    for (const turn of interruptedTurns) {
      const reason = "MAGI restarted; this turn was interrupted and was not retried. Tool side effects may have completed; check before retrying.";
      this.agentTurns.interrupt(turn.id, reason);
      this.agentTurns.evict(turn.id);
      const input = JSON.parse(turn.input) as JobInput["MessageDeliveryJob"];
      const message = this.messages.get(input.message_id);
      if (message) messageDelivery.reportError(this, reason, { module: turn.worker ?? "agent", source: `restart:${turn.id}`, chat_id: message.chat_id, wake: !messageDelivery.isError(this, message.id) });
    }
    this.deliveries.reconcile(this);
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
