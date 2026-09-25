import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import type { Database } from "bun:sqlite";
import { drizzle, type BunSQLiteDatabase } from "drizzle-orm/bun-sqlite";
import { migrate } from "drizzle-orm/bun-sqlite/migrator";

/** Either workspace database. Each Book declares the table it owns. */
export type BusDb = BunSQLiteDatabase & { $client: Database };

/** Wrap an already open connection: the caller owns PRAGMAs and the py-magi guard. */
export function workspaceDatabase(client: Database): BusDb {
  return drizzle(client);
}

/** Bring a workspace database up to the schema the code expects. */
export function migrateBooks(db: BusDb): void {
  migrate(db, { migrationsFolder: migrationsFolder("memories") });
}

export function migrateJobs(db: BusDb): void {
  migrate(db, { migrationsFolder: migrationsFolder("logs") });
}

// ``bus/drizzle/`` is a sibling of this file's folder; the second shape covers a
// compiled copy under ``dist/``, which tsc emits without copying the .sql files.
function migrationsFolder(name: string): string {
  for (let dir = import.meta.dir; ; dir = dirname(dir)) {
    for (const candidate of [join(dir, "drizzle", name), join(dir, "bus", "drizzle", name)]) {
      if (existsSync(candidate)) return candidate;
    }
    if (dirname(dir) === dir) throw new Error(`drizzle/${name} migrations are missing`);
  }
}
