/** Desktop conversation history under ~/.magi/app/. */

import { mkdirSync } from "node:fs";
import path from "node:path";

export async function openChatStore(file) {
  const { DatabaseSync } = await import("node:sqlite");
  mkdirSync(path.dirname(file), { recursive: true });
  const db = new DatabaseSync(file);
  db.exec("PRAGMA journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS conversations (
      id TEXT PRIMARY KEY,
      record_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
      conversation_id TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      record_json TEXT NOT NULL,
      PRIMARY KEY (conversation_id, sequence)
    );
  `);
  const saveConversation = db.prepare(
    "INSERT INTO conversations VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET record_json = excluded.record_json",
  );
  const saveEvent = db.prepare("INSERT OR IGNORE INTO events VALUES (?, ?, ?)");
  const readConversations = db.prepare("SELECT record_json FROM conversations");
  const readEvents = db.prepare(
    "SELECT record_json FROM events WHERE conversation_id = ? ORDER BY sequence",
  );
  const readLastSequence = db.prepare(
    "SELECT max(sequence) AS sequence FROM events WHERE conversation_id = ?",
  );

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
    saveConversations(conversations) {
      transaction(() => {
        for (const conversation of conversations) {
          if (typeof conversation?.conversation_id === "string") {
            saveConversation.run(conversation.conversation_id, JSON.stringify(conversation));
          }
        }
      });
    },
    listConversations() {
      return readConversations.all().map((row) => JSON.parse(row.record_json));
    },
    saveEvents(conversationId, events) {
      transaction(() => {
        for (const event of events) {
          if (Number.isSafeInteger(event?.sequence) && event.sequence >= 0) {
            saveEvent.run(conversationId, event.sequence, JSON.stringify(event));
          }
        }
      });
      return readLastSequence.get(conversationId)?.sequence ?? -1;
    },
    listEvents(conversationId) {
      return readEvents.all(conversationId).map((row) => JSON.parse(row.record_json));
    },
    lastSequence(conversationId) {
      return readLastSequence.get(conversationId)?.sequence ?? -1;
    },
    close() {
      db.close();
    },
  };
}
