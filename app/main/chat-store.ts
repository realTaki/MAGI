/** Desktop chat history under ~/.magi/app/. */

import { mkdirSync } from "node:fs";
import path from "node:path";

export async function openChatStore(file) {
  const { DatabaseSync } = await import("node:sqlite");
  mkdirSync(path.dirname(file), { recursive: true });
  const db = new DatabaseSync(file);
  db.exec("PRAGMA journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS chats (
      id TEXT PRIMARY KEY,
      record_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
      chat_id TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      record_json TEXT NOT NULL,
      PRIMARY KEY (chat_id, sequence)
    );
    CREATE TABLE IF NOT EXISTS acknowledgements (
      chat_id TEXT PRIMARY KEY,
      through_sequence INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS outgoing_messages (
      id TEXT PRIMARY KEY,
      chat_id TEXT NOT NULL,
      content TEXT NOT NULL,
      created_at INTEGER NOT NULL
    );
  `);
  const saveChat = db.prepare(
    "INSERT INTO chats VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET record_json = excluded.record_json",
  );
  const saveEvent = db.prepare("INSERT OR IGNORE INTO events VALUES (?, ?, ?)");
  const readChats = db.prepare("SELECT record_json FROM chats");
  const readEvents = db.prepare(
    "SELECT record_json FROM events WHERE chat_id = ? ORDER BY sequence",
  );
  const readLastSequence = db.prepare(
    "SELECT max(sequence) AS sequence FROM events WHERE chat_id = ?",
  );
  const readPendingAcks = db.prepare(`
    SELECT e.record_json FROM events e
    WHERE e.chat_id = ? AND e.sequence > COALESCE((
      SELECT through_sequence FROM acknowledgements WHERE chat_id = e.chat_id
    ), -1)
    ORDER BY e.sequence LIMIT 500
  `);
  const markAcknowledged = db.prepare(`
    INSERT INTO acknowledgements VALUES (?, ?)
    ON CONFLICT(chat_id) DO UPDATE SET
      through_sequence = max(through_sequence, excluded.through_sequence)
  `);
  const saveOutgoingMessage = db.prepare(
    "INSERT OR IGNORE INTO outgoing_messages VALUES (?, ?, ?, ?)",
  );
  const readOutgoingMessages = db.prepare(
    "SELECT id, chat_id, content, created_at FROM outgoing_messages ORDER BY created_at, id",
  );
  const deleteOutgoingMessage = db.prepare("DELETE FROM outgoing_messages WHERE id = ?");

  function transaction(write) {
    db.exec("BEGIN IMMEDIATE");
    try {
      write();
      db.exec("COMMIT");
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  }

  return {
    saveChats(chats) {
      transaction(() => {
        for (const chat of chats) {
          if (typeof chat?.chat_id === "string") {
            saveChat.run(chat.chat_id, JSON.stringify(chat));
          }
        }
      });
    },
    listChats() {
      return readChats.all().map((row) => JSON.parse(row.record_json));
    },
    saveEvents(chatId, events) {
      transaction(() => {
        for (const event of events) {
          if (Number.isSafeInteger(event?.sequence) && event.sequence >= 0) {
            saveEvent.run(chatId, event.sequence, JSON.stringify(event));
          }
        }
      });
      return readLastSequence.get(chatId)?.sequence ?? -1;
    },
    listEvents(chatId) {
      return readEvents.all(chatId).map((row) => JSON.parse(row.record_json));
    },
    lastSequence(chatId) {
      return readLastSequence.get(chatId)?.sequence ?? -1;
    },
    pendingAcks(chatId) {
      return readPendingAcks.all(chatId).map((row) => JSON.parse(row.record_json));
    },
    markAcknowledged(chatId, sequence) {
      markAcknowledged.run(chatId, sequence);
    },
    queueOutgoingMessage(message) {
      if (
        typeof message?.id !== "string" || typeof message?.chatId !== "string"
        || typeof message?.content !== "string" || !Number.isSafeInteger(message?.createdAt)
      ) {
        throw new Error("Invalid outgoing message");
      }
      saveOutgoingMessage.run(message.id, message.chatId, message.content, message.createdAt);
    },
    listOutgoingMessages() {
      return readOutgoingMessages.all().map((row) => ({
        id: row.id,
        chatId: row.chat_id,
        content: row.content,
        createdAt: row.created_at,
      }));
    },
    removeOutgoingMessage(id) {
      deleteOutgoingMessage.run(id);
    },
    close() {
      // Windows keeps the directory locked while a prepared statement or the
      // WAL file is still open, so a test cannot remove its temp folder.
      try {
        db.exec("PRAGMA wal_checkpoint(TRUNCATE)");
      } catch {
        // The connection is already shutting down.
      }
      for (const statement of [
        saveChat,
        saveEvent,
        readChats,
        readEvents,
        readLastSequence,
        readPendingAcks,
        markAcknowledged,
        saveOutgoingMessage,
        readOutgoingMessages,
        deleteOutgoingMessage,
      ]) {
        if (typeof statement.finalize === "function") {
          statement.finalize();
        }
      }
      db.close();
    },
  };
}
