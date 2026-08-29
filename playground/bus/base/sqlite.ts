import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";

export class Sqlite {
  readonly database: Database.Database;

  private constructor(file: string) {
    mkdirSync(dirname(file), { recursive: true });
    this.database = new Database(file);
    this.database.pragma("foreign_keys = ON");
    this.database.pragma("journal_mode = WAL");
  }

  static async open(file: string): Promise<Sqlite> {
    const sqlite = new Sqlite(file);
    try {
      migrate(drizzle(sqlite.database), {
        migrationsFolder: fileURLToPath(new URL("../../drizzle", import.meta.url)),
      });
      return sqlite;
    } catch (error) {
      sqlite.close();
      throw error;
    }
  }

  transaction<T>(work: () => T): T {
    return this.database.transaction(work).immediate();
  }

  close(): void {
    this.database.close();
  }
}
