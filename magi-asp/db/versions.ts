/** Ordered, idempotent schema upgrades for the ASP sqlite file. */

import type { DatabaseSync } from "node:sqlite";

type Migration = (connection: DatabaseSync) => void;

function version1(connection: DatabaseSync): void {
  connection.exec(`
    CREATE TABLE asp_settings (
      key TEXT PRIMARY KEY,
      value_json TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    );
  `);
}

function version2(connection: DatabaseSync): void {
  connection.exec(`
    CREATE TABLE asp_agents (handle TEXT PRIMARY KEY, record_json TEXT NOT NULL);
    CREATE TABLE asp_sessions (
      id TEXT PRIMARY KEY,
      record_json TEXT NOT NULL,
      next_sequence INTEGER NOT NULL
    );
    CREATE TABLE asp_participants (
      session_id TEXT NOT NULL,
      handle TEXT NOT NULL,
      record_json TEXT NOT NULL,
      PRIMARY KEY (session_id, handle)
    );
    CREATE TABLE asp_events (
      session_id TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      event_id TEXT NOT NULL UNIQUE,
      type TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      payload_json TEXT NOT NULL,
      PRIMARY KEY (session_id, sequence)
    );
    CREATE TABLE asp_message_recipients (
      event_id TEXT NOT NULL,
      handle TEXT NOT NULL,
      acked INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (event_id, handle),
      FOREIGN KEY (event_id) REFERENCES asp_events(event_id) ON DELETE CASCADE
    );
    CREATE TABLE asp_delivery_acks (
      session_id TEXT NOT NULL,
      handle TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      PRIMARY KEY (session_id, handle)
    );
    CREATE TABLE asp_message_keys (
      session_id TEXT NOT NULL,
      sender TEXT NOT NULL,
      key TEXT NOT NULL,
      message_id TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      PRIMARY KEY (session_id, sender, key)
    );
    CREATE TABLE asp_session_keys (
      creator TEXT NOT NULL,
      key TEXT NOT NULL,
      session_id TEXT NOT NULL,
      sequence INTEGER,
      PRIMARY KEY (creator, key)
    );
  `);
}

function version3(connection: DatabaseSync): void {
  connection.exec(`
    CREATE TABLE asp_event_acks (
      event_id TEXT NOT NULL,
      handle TEXT NOT NULL,
      PRIMARY KEY (event_id, handle),
      FOREIGN KEY (event_id) REFERENCES asp_events(event_id) ON DELETE CASCADE
    );
    DROP TABLE IF EXISTS asp_delivery_acks;
  `);
}

const MIGRATIONS: readonly Migration[] = [version1, version2, version3];

export function transaction(connection: DatabaseSync, apply: () => void): void {
  connection.exec("BEGIN");
  try {
    apply();
    connection.exec("COMMIT");
  } catch (error) {
    try {
      connection.exec("ROLLBACK");
    } catch {
      // The original error is the one the caller needs.
    }
    throw error;
  }
}

export function applyMigrations(connection: DatabaseSync): void {
  connection.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      applied_at INTEGER NOT NULL
    )
  `);
  const applied = new Set(
    connection
      .prepare("SELECT version FROM schema_migrations")
      .all()
      .map((row) => Number(row.version)),
  );
  MIGRATIONS.forEach((migration, index) => {
    const version = index + 1;
    if (applied.has(version)) {
      return;
    }
    transaction(connection, () => {
      migration(connection);
      connection
        .prepare(
          "INSERT INTO schema_migrations (version, applied_at) VALUES (?, unixepoch() * 1000)",
        )
        .run(version);
    });
  });
}
