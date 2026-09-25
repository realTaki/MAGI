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

/** Rename the fixed operator identity without invalidating existing sessions. */
function version4(connection: DatabaseSync): void {
  const legacy = "user";
  const operator = "@user";
  const setting = connection
    .prepare("SELECT value_json FROM asp_settings WHERE key = 'operator'")
    .get() as { value_json?: unknown } | undefined;
  if (typeof setting?.value_json === "string") {
    const parsed: unknown = JSON.parse(setting.value_json);
    if (isRecord(parsed) && (parsed.handle === legacy || parsed.handle === undefined)) {
      connection.prepare("UPDATE asp_settings SET value_json = ?, updated_at = unixepoch() * 1000 WHERE key = 'operator'")
        .run(JSON.stringify({ ...parsed, handle: operator }));
    }
  }

  const agent = connection
    .prepare("SELECT record_json FROM asp_agents WHERE handle = ?")
    .get(legacy) as { record_json?: unknown } | undefined;
  if (agent !== undefined) {
    const record = typeof agent.record_json === "string" ? JSON.parse(agent.record_json) : {};
    const rewritten = isRecord(record) ? { ...record, handle: operator } : { handle: operator };
    connection.prepare("UPDATE asp_agents SET handle = ?, record_json = ? WHERE handle = ?")
      .run(operator, JSON.stringify(rewritten), legacy);
  }

  for (const table of ["asp_participants", "asp_message_recipients", "asp_event_acks"]) {
    connection.prepare(`UPDATE ${table} SET handle = ? WHERE handle = ?`).run(operator, legacy);
  }
  connection.prepare("UPDATE asp_message_keys SET sender = ? WHERE sender = ?").run(operator, legacy);
  connection.prepare("UPDATE asp_session_keys SET creator = ? WHERE creator = ?").run(operator, legacy);

  const events = connection.prepare("SELECT event_id, payload_json FROM asp_events").all() as Array<{
    event_id: unknown;
    payload_json: unknown;
  }>;
  for (const event of events) {
    if (typeof event.event_id !== "string" || typeof event.payload_json !== "string") continue;
    const payload: unknown = JSON.parse(event.payload_json);
    if (!isRecord(payload)) continue;
    let changed = false;
    const next = { ...payload };
    for (const key of ["sender", "by", "agent", "invitee", "ended_by", "reopened_by"]) {
      if (next[key] === legacy) {
        next[key] = operator;
        changed = true;
      }
    }
    if (changed) {
      connection.prepare("UPDATE asp_events SET payload_json = ? WHERE event_id = ?")
        .run(JSON.stringify(next), event.event_id);
    }
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const MIGRATIONS: readonly Migration[] = [version1, version2, version3, version4];

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
