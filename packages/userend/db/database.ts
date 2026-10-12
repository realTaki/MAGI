/** Versioned SQLite cache owned by the desktop operator application. */

import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";

import Database from "better-sqlite3";
import type { Database as SQLiteDatabase } from "better-sqlite3";
import { and, asc, eq, gt, isNull, max, or, sql } from "drizzle-orm";
import { drizzle, type BetterSQLite3Database } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";

import { appAcknowledgements, appChats, appEvents, appOutgoingMessages } from "./tables/chatCache.ts";

type AppDb = BetterSQLite3Database & { $client: SQLiteDatabase };

export type OutgoingMessage = {
  id: string;
  chatId: string;
  content: string;
  createdAt: number;
};

/** The public cache interface used by the desktop bridge and its tests. */
export type ChatStore = ReturnType<typeof openChatStore>;

export function openChatStore(file: string) {
  mkdirSync(path.dirname(file), { recursive: true });
  const connection = new Database(file);
  connection.pragma("foreign_keys = ON");
  connection.pragma("journal_mode = WAL");
  connection.pragma("busy_timeout = 5000");
  const db: AppDb = drizzle(connection);
  migrate(db, { migrationsFolder: migrationsFolder() });

  const transaction = connection.transaction((write: () => void) => write());

  return {
    saveChats(chats: unknown[]) {
      transaction(() => {
        for (const chat of chats) {
          if (!isChat(chat)) continue;
          db.insert(appChats).values({ id: chat.chat_id, record_json: chat })
            .onConflictDoUpdate({ target: appChats.id, set: { record_json: chat } })
            .run();
        }
      });
    },
    listChats(): unknown[] {
      return db.select({ record: appChats.record_json }).from(appChats).all().map((row) => row.record);
    },
    saveEvents(chatId: string, events: unknown[]): number {
      transaction(() => {
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
      return db.select({ record: appEvents.record_json }).from(appEvents)
        .leftJoin(appAcknowledgements, eq(appEvents.chat_id, appAcknowledgements.chat_id))
        .where(and(
          eq(appEvents.chat_id, chatId),
          or(isNull(appAcknowledgements.through_sequence), gt(appEvents.sequence, appAcknowledgements.through_sequence)),
        ))
        .orderBy(asc(appEvents.sequence)).limit(500).all().map((row) => row.record);
    },
    markAcknowledged(chatId: string, sequence: number): void {
      db.insert(appAcknowledgements).values({ chat_id: chatId, through_sequence: sequence })
        .onConflictDoUpdate({
          target: appAcknowledgements.chat_id,
          set: { through_sequence: sql`max(${appAcknowledgements.through_sequence}, excluded.through_sequence)` },
        })
        .run();
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
        connection.pragma("wal_checkpoint(TRUNCATE)");
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

function isChat(value: unknown): value is { chat_id: string } {
  return typeof value === "object" && value !== null && typeof (value as { chat_id?: unknown }).chat_id === "string";
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

// The source tree owns db/drizzle. Walking upward also supports a bundled copy
// loaded from a managed App checkout, matching ASP and BUS migration lookup.
function migrationsFolder(): string {
  for (let directory = import.meta.dirname; ; directory = path.dirname(directory)) {
    const candidate = path.join(directory, "drizzle");
    if (existsSync(path.join(candidate, "meta", "_journal.json"))) return candidate;
    if (path.dirname(directory) === directory) break;
  }
  throw new Error("Desktop database migrations are missing (db/drizzle)");
}
