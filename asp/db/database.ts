/** The durable SQLite connection owned by a running ASP server process. */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

import Database from "better-sqlite3";
import type { Database as SQLiteDatabase } from "better-sqlite3";
import { eq } from "drizzle-orm";
import { drizzle, type BetterSQLite3Database } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";

import { repairLegacyOperatorHandle } from "./legacyOperator.ts";
import { aspSettings } from "./tables/settings.ts";

/** The relay's drizzle client over a better-sqlite3 connection. */
export type AspDb = BetterSQLite3Database & { $client: SQLiteDatabase };

export function defaultDataDir(): string {
  return path.join(homedir(), ".magi", "asp");
}

export function defaultDatabasePath(): string {
  return path.join(defaultDataDir(), "asp.sqlite");
}

export class LocalDatabase {
  path: string;
  connection: SQLiteDatabase | null = null;
  db: AspDb | null = null;

  constructor(databasePath: string) {
    this.path = databasePath;
  }

  open(): void {
    if (this.connection !== null) {
      return;
    }
    mkdirSync(path.dirname(this.path), { recursive: true });
    this.#copyLegacyFile();
    const connection = new Database(this.path);
    connection.exec("PRAGMA foreign_keys = ON");
    connection.exec("PRAGMA journal_mode = WAL");
    const db = drizzle(connection);
    // The baseline is idempotent, so a database from the hand-written migration
    // era is adopted here: nothing is created twice, and the run is recorded.
    migrate(db, { migrationsFolder: migrationsFolder() });
    repairLegacyOperatorHandle(db);
    this.connection = connection;
    this.db = db;
  }

  close(): void {
    if (this.connection === null) {
      return;
    }
    this.connection.close();
    this.connection = null;
    this.db = null;
  }

  /** Settings are the relay's own key/value rows; values are JSON. */
  getSetting(key: string): unknown {
    return this.#client().select({ value: aspSettings.value_json }).from(aspSettings)
      .where(eq(aspSettings.key, key)).get()?.value ?? null;
  }

  setSetting(key: string, value: unknown): void {
    const updated_at = Date.now();
    this.#client().insert(aspSettings)
      .values({ key, value_json: value, updated_at })
      .onConflictDoUpdate({ target: aspSettings.key, set: { value_json: value, updated_at } })
      .run();
  }

  deleteSetting(key: string): void {
    this.#client().delete(aspSettings).where(eq(aspSettings.key, key)).run();
  }

  #client(): AspDb {
    if (this.db === null) {
      throw new Error("LocalDatabase is not open");
    }
    return this.db;
  }

  #copyLegacyFile(): void {
    const legacy = path.join(homedir(), ".magi", "asp.sqlite");
    if (
      path.resolve(this.path) !== path.resolve(defaultDatabasePath()) ||
      existsSync(this.path) ||
      !existsSync(legacy)
    ) {
      return;
    }
    const source = new Database(legacy, { readonly: true });
    try {
      writeFileSync(this.path, source.serialize());
    } finally {
      source.close();
    }
  }
}

// ``db/drizzle/`` holds the migrations; the walk also covers a copy run from
// somewhere else in the tree, and stops at the filesystem root.
function migrationsFolder(): string {
  for (let dir = import.meta.dirname; ; dir = path.dirname(dir)) {
    const candidate = path.join(dir, "drizzle");
    if (existsSync(path.join(candidate, "meta", "_journal.json"))) {
      return candidate;
    }
    if (path.dirname(dir) === dir) {
      throw new Error("ASP migrations are missing (db/drizzle)");
    }
  }
}
