/** Versioned SQLite cache owned by webapp: events and the outbound queue. */

import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";

import type { Bus } from "@magi/bus";
import { drizzle, migrate, openDatabase, pragma, transaction, type SqliteDb } from "@magi/bus/sqlite.js";
import { and, asc, eq, gt, max } from "drizzle-orm";

import { ackThrough, listChats as listDirectory, markAcknowledged as ackChat, saveChats as saveDirectory } from "../directory.js";
import { appEvents, appOutgoingMessages } from "./tables/chatCache.js";

type AppDb = SqliteDb;

export type OutgoingMessage = {
  id: string;
  chatId: string;
  content: string;
  createdAt: number;
};

/** The public cache interface used by the desktop bridge. */
export type ChatStore = ReturnType<typeof openChatStore>;

export function openChatStore(file: string, bus: Bus) {
  mkdirSync(path.dirname(file), { recursive: true });
  const connection = openDatabase(file);
  const db: AppDb = drizzle(connection);
  try {
    pragma(connection, "foreign_keys = ON");
    pragma(connection, "journal_mode = WAL");
    pragma(connection, "busy_timeout = 5000");
    migrate(db, migrationsFolder());
  } catch (error) {
    connection.close();
    throw error;
  }

  return {
    saveChats(chats: unknown[]) {
      saveDirectory(bus, chats);
    },
    listChats(): unknown[] {
      return listDirectory(bus);
    },
    saveEvents(chatId: string, events: unknown[]): number {
      transaction(connection, () => {
        for (const event of events) {
          if (!isEvent(event)) continue;
          db.insert(appEvents).values({ chat_id: chatId, sequence: event.sequence, record_json: event })
            .onConflictDoNothing()
            .run();
        }
      });
      return lastSequence(db, chatId);
    },
    listEvents(chatId: string): unknown[] {
      return db.select({ record: appEvents.record_json }).from(appEvents)
        .where(eq(appEvents.chat_id, chatId)).orderBy(asc(appEvents.sequence)).all()
        .map((row) => row.record);
    },
    lastSequence(chatId: string): number {
      return lastSequence(db, chatId);
    },
    pendingAcks(chatId: string): unknown[] {
      const through = ackThrough(bus, chatId);
      return db.select({ record: appEvents.record_json }).from(appEvents)
        .where(and(eq(appEvents.chat_id, chatId), gt(appEvents.sequence, through)))
        .orderBy(asc(appEvents.sequence)).limit(500).all().map((row) => row.record);
    },
    markAcknowledged(chatId: string, sequence: number): void {
      ackChat(bus, chatId, sequence);
    },
    queueOutgoingMessage(message: OutgoingMessage): void {
      if (!isOutgoingMessage(message)) throw new Error("Invalid outgoing message");
      db.insert(appOutgoingMessages).values({
        id: message.id, chat_id: message.chatId, content: message.content, created_at: message.createdAt,
      }).onConflictDoNothing().run();
    },
    listOutgoingMessages(): OutgoingMessage[] {
      return db.select().from(appOutgoingMessages).orderBy(asc(appOutgoingMessages.created_at), asc(appOutgoingMessages.id))
        .all().map((row) => ({ id: row.id, chatId: row.chat_id, content: row.content, createdAt: row.created_at }));
    },
    removeOutgoingMessage(id: string): void {
      db.delete(appOutgoingMessages).where(eq(appOutgoingMessages.id, id)).run();
    },
    close(): void {
      try {
        pragma(connection, "wal_checkpoint(TRUNCATE)");
      } catch {
        // The connection is already shutting down.
      }
      connection.close();
    },
  };
}

function lastSequence(db: AppDb, chatId: string): number {
  return db.select({ sequence: max(appEvents.sequence) }).from(appEvents)
    .where(eq(appEvents.chat_id, chatId)).get()?.sequence ?? -1;
}

function isEvent(value: unknown): value is { sequence: number } {
  const sequence = typeof value === "object" && value !== null ? (value as { sequence?: unknown }).sequence : undefined;
  return typeof sequence === "number" && Number.isSafeInteger(sequence) && sequence >= 0;
}

function isOutgoingMessage(value: unknown): value is OutgoingMessage {
  if (typeof value !== "object" || value === null) return false;
  const message = value as Partial<OutgoingMessage>;
  return typeof message.id === "string" && typeof message.chatId === "string"
    && typeof message.content === "string" && Number.isSafeInteger(message.createdAt);
}

// ``db/drizzle/`` holds the migrations. The walk covers a compiled copy under
// ``dist/``, which tsc emits without copying the .sql files, and prefers source.
function migrationsFolder(): string {
  let bundled: string | null = null;
  for (let dir = import.meta.dirname; ; dir = path.dirname(dir)) {
    for (const candidate of [path.join(dir, "drizzle"), path.join(dir, "db", "drizzle")]) {
      if (!existsSync(path.join(candidate, "meta", "_journal.json"))) continue;
      if (!candidate.includes("/dist/")) return candidate;
      bundled ??= candidate;
    }
    if (path.dirname(dir) === dir) break;
  }
  if (bundled) return bundled;
  throw new Error("Desktop database migrations are missing (db/drizzle)");
}
