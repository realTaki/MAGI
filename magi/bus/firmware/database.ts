import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import type { Database } from "bun:sqlite";
import { drizzle, type BunSQLiteDatabase } from "drizzle-orm/bun-sqlite";
import { migrate } from "drizzle-orm/bun-sqlite/migrator";
import * as books from "./schema.js";
import * as jobs from "./jobs/schema.js";

export type BooksDb = BunSQLiteDatabase<typeof books> & { $client: Database };
export type JobsDb = BunSQLiteDatabase<typeof jobs> & { $client: Database };

/** Wrap an already open connection: the caller owns PRAGMAs and the py-magi guard. */
export function booksDatabase(client: Database): BooksDb {
  return drizzle(client, { schema: books });
}

export function jobsDatabase(client: Database): JobsDb {
  return drizzle(client, { schema: jobs });
}

/** Bring a workspace database up to the schema the code expects. */
export function migrateBooks(db: BooksDb): void {
  migrate(db, { migrationsFolder: migrationsFolder("memories") });
}

export function migrateJobs(db: JobsDb): void {
  migrate(db, { migrationsFolder: migrationsFolder("logs") });
}

// ``drizzle/`` sits at the package root; the walk also covers a compiled copy under dist/.
function migrationsFolder(name: string): string {
  for (let dir = import.meta.dir; ; dir = dirname(dir)) {
    const candidate = join(dir, "drizzle", name);
    if (existsSync(candidate)) return candidate;
    if (dirname(dir) === dir) throw new Error(`drizzle/${name} migrations are missing`);
  }
}
