/**
 * Local, Webapp-owned persistence.
 *
 * This database belongs to the MAGI Webapp installation, rather than to an
 * individual remote MAGI.  Remote data can be cached here, but its canonical
 * copy continues to live in the selected MAGI runtime.
 */
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { join } from "node:path";
import initSqlJs from "sql.js";

const LATEST_SCHEMA_VERSION = 1;
const require = createRequire(import.meta.url);
const sqlPromise = initSqlJs({
  locateFile: (file) => require.resolve(`sql.js/dist/${file}`),
});

export function defaultAppDataDir() {
  return join(homedir(), ".magi", "app");
}

export function defaultAppDatabasePath() {
  return join(defaultAppDataDir(), "app.sqlite");
}

function ensurePrivateDirectory(directory) {
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  // mkdir respects the process umask and does not change an existing
  // directory.  The store contains local drafts and credential references,
  // so make the intended Unix permission explicit.
  if (process.platform !== "win32") chmodSync(directory, 0o700);
}

function queryRows(db, statement, parameters = []) {
  const result = db.exec(statement, parameters)[0];
  if (!result) return [];
  return result.values.map((values) => Object.fromEntries(result.columns.map((column, index) => [column, values[index]])));
}

function applyMigrations(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      applied_at INTEGER NOT NULL
    );
  `);

  const applied = new Set(queryRows(db, "SELECT version FROM schema_migrations").map(({ version }) => version));

  if (!applied.has(1)) {
    db.run("BEGIN IMMEDIATE");
    try {
      db.exec(`
        CREATE TABLE app_settings (
          key TEXT PRIMARY KEY,
          value_json TEXT NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE conversations (
          id TEXT PRIMARY KEY,
          magi_id TEXT NOT NULL,
          remote_id TEXT,
          title TEXT NOT NULL DEFAULT '',
          sync_cursor TEXT,
          remote_updated_at INTEGER,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE UNIQUE INDEX conversations_remote_id
          ON conversations(magi_id, remote_id)
          WHERE remote_id IS NOT NULL;
        CREATE INDEX conversations_by_magi_updated
          ON conversations(magi_id, updated_at DESC);
      `);
      db.run("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", [1, Date.now()]);
      db.run("COMMIT");
    } catch (error) {
      db.run("ROLLBACK");
      throw error;
    }
  }

  const current = queryRows(db, "SELECT MAX(version) AS version FROM schema_migrations")[0].version ?? 0;
  if (current !== LATEST_SCHEMA_VERSION) {
    throw new Error(`Unsupported App database schema version: ${current}`);
  }
}

/**
 * Opens the App database and returns only App-owned persistence operations.
 *
 * `dataDir` exists for the browser-hosted deployment and for tests.  In a
 * normal desktop installation it resolves to `~/.magi/app`.
 */
export async function openAppStore({ dataDir = defaultAppDataDir() } = {}) {
  ensurePrivateDirectory(dataDir);
  const databasePath = join(dataDir, "app.sqlite");
  const SQL = await sqlPromise;
  const db = existsSync(databasePath) ? new SQL.Database(readFileSync(databasePath)) : new SQL.Database();
  applyMigrations(db);

  // sql.js keeps SQLite's page store in memory. Webapp Core is deliberately its
  // single owner at this stage, so each mutation atomically replaces the on-
  // disk database and no second process may open it for writes.
  function persist() {
    const temporaryPath = `${databasePath}.tmp`;
    writeFileSync(temporaryPath, db.export(), { mode: 0o600 });
    if (process.platform !== "win32") chmodSync(temporaryPath, 0o600);
    renameSync(temporaryPath, databasePath);
    if (process.platform !== "win32") chmodSync(databasePath, 0o600);
  }

  persist();

  return {
    databasePath,

    getSetting(key) {
      const row = queryRows(db, "SELECT value_json FROM app_settings WHERE key = ?", [key])[0];
      return row ? JSON.parse(row.value_json) : undefined;
    },

    setSetting(key, value) {
      db.run(`
        INSERT INTO app_settings (key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
          value_json = excluded.value_json,
          updated_at = excluded.updated_at
      `, [key, JSON.stringify(value), Date.now()]);
      persist();
    },

    listConversations(magiId) {
      return queryRows(db, `
        SELECT id, magi_id AS magiId, remote_id AS remoteId, title,
               sync_cursor AS syncCursor, remote_updated_at AS remoteUpdatedAt,
               created_at AS createdAt, updated_at AS updatedAt
        FROM conversations
        WHERE magi_id = ?
        ORDER BY updated_at DESC
      `, [magiId]);
    },

    saveConversation(conversation) {
      const now = Date.now();
      db.run(`
        INSERT INTO conversations (
          id, magi_id, remote_id, title, sync_cursor, remote_updated_at, created_at, updated_at
        ) VALUES (
          @id, @magiId, @remoteId, @title, @syncCursor, @remoteUpdatedAt, @createdAt, @updatedAt
        )
        ON CONFLICT(id) DO UPDATE SET
          magi_id = excluded.magi_id,
          remote_id = excluded.remote_id,
          title = excluded.title,
          sync_cursor = excluded.sync_cursor,
          remote_updated_at = excluded.remote_updated_at,
          updated_at = excluded.updated_at
      `, [
        conversation.id,
        conversation.magiId,
        conversation.remoteId ?? null,
        conversation.title ?? "",
        conversation.syncCursor ?? null,
        conversation.remoteUpdatedAt ?? null,
        conversation.createdAt ?? now,
        now,
      ]);
      persist();
    },

    close() {
      db.close();
    },
  };
}
