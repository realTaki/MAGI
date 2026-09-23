/** The durable SQLite connection owned by a running ASP server process. */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import { applyMigrations, transaction } from "./versions.ts";

export function defaultDataDir(): string {
  return path.join(homedir(), ".magi", "asp");
}

export function defaultDatabasePath(): string {
  return path.join(defaultDataDir(), "asp.sqlite");
}

export class LocalDatabase {
  path: string;
  connection: DatabaseSync | null = null;

  constructor(databasePath: string) {
    this.path = databasePath;
  }

  open(): void {
    if (this.connection !== null) {
      return;
    }
    mkdirSync(path.dirname(this.path), { recursive: true });
    this.#copyLegacyFile();
    const connection = new DatabaseSync(this.path);
    connection.exec("PRAGMA foreign_keys = ON");
    connection.exec("PRAGMA journal_mode = WAL");
    applyMigrations(connection);
    this.connection = connection;
  }

  close(): void {
    if (this.connection === null) {
      return;
    }
    this.connection.close();
    this.connection = null;
  }

  getSetting(key: string): unknown {
    const row = this.#connection()
      .prepare("SELECT value_json FROM asp_settings WHERE key = ?")
      .get(key);
    if (row === undefined) {
      return null;
    }
    const value = row.value_json;
    if (typeof value !== "string") {
      throw new Error("asp_settings.value_json is not text");
    }
    return JSON.parse(value);
  }

  setSetting(key: string, value: unknown): void {
    const connection = this.#connection();
    transaction(connection, () => {
      connection
        .prepare(
          `INSERT INTO asp_settings (key, value_json, updated_at)
           VALUES (?, ?, unixepoch() * 1000)
           ON CONFLICT(key) DO UPDATE SET
             value_json = excluded.value_json,
             updated_at = excluded.updated_at`,
        )
        .run(key, JSON.stringify(value));
    });
  }

  deleteSetting(key: string): void {
    const connection = this.#connection();
    transaction(connection, () => {
      connection.prepare("DELETE FROM asp_settings WHERE key = ?").run(key);
    });
  }

  #connection(): DatabaseSync {
    if (this.connection === null) {
      throw new Error("LocalDatabase is not open");
    }
    return this.connection;
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
    const source = new DatabaseSync(legacy, { readOnly: true });
    try {
      writeFileSync(this.path, snapshot(source));
    } finally {
      source.close();
    }
  }
}

function snapshot(source: DatabaseSync): Uint8Array {
  if (!canSerialize(source)) {
    throw new Error("node:sqlite cannot snapshot the legacy database");
  }
  const bytes: unknown = source.serialize();
  if (!(bytes instanceof Uint8Array)) {
    throw new Error("legacy database snapshot is not bytes");
  }
  return bytes;
}

function canSerialize(source: DatabaseSync): source is DatabaseSync & { serialize: () => unknown } {
  return "serialize" in source && typeof source.serialize === "function";
}
