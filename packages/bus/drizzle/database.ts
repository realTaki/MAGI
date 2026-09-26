import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Database as SQLiteDatabase } from "better-sqlite3";
import { drizzle, type BetterSQLite3Database } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";

/** Either workspace database. Each Book declares the table it owns. */
export type BusDb = BetterSQLite3Database & { $client: SQLiteDatabase };

/** Wrap an already open connection: the caller owns opening it and its PRAGMAs. */
export function workspaceDatabase(client: SQLiteDatabase): BusDb {
  return drizzle(client);
}

/** Bring a workspace database up to the schema the code expects. */
export function migrateBooks(db: BusDb): void {
  migrate(db, { migrationsFolder: migrationsFolder("books") });
}

export function migrateJobs(db: BusDb): void {
  migrate(db, { migrationsFolder: migrationsFolder("jobs") });
}

// ``bus/drizzle/`` holds this file next to the migrations; the second shape covers
// a compiled copy under ``dist/``, which tsc emits without copying the .sql files.
function migrationsFolder(name: string): string {
  let bundled: string | null = null;
  for (let dir = dirname(fileURLToPath(import.meta.url)); ; dir = dirname(dir)) {
    for (const candidate of [join(dir, "drizzle", name), join(dir, "bus", "drizzle", name)]) {
      if (!existsSync(candidate)) continue;
      // In a source workspace tsc leaves an older copied migration folder under
      // dist/. Prefer the source folder so a newly added SQL migration is visible.
      if (!candidate.includes("/dist/")) return candidate;
      bundled ??= candidate;
    }
    if (dirname(dir) === dir) break;
  }
  if (bundled) return bundled;
  throw new Error(`drizzle/${name} migrations are missing`);
}
