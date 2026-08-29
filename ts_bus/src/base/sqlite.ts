import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

import { applyMigrations } from "./migrations.js";

export class Sqlite {
  readonly database: DatabaseSync;

  private constructor(file: string) {
    mkdirSync(dirname(file), { recursive: true });
    this.database = new DatabaseSync(file);
    this.database.exec("PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL;");
  }

  static async open(file: string): Promise<Sqlite> {
    const sqlite = new Sqlite(file);
    try {
      await applyMigrations(sqlite.database, fileURLToPath(new URL("../../drizzle", import.meta.url)));
      return sqlite;
    } catch (error) {
      sqlite.close();
      throw error;
    }
  }

  transaction<T>(work: () => T): T {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const result = work();
      this.database.exec("COMMIT");
      return result;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }

  close(): void {
    this.database.close();
  }
}
