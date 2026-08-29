import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import type { DatabaseSync } from "node:sqlite";

/** Applies Drizzle-generated SQL exactly once and rejects edited history. */
export async function applyMigrations(database: DatabaseSync, folder: string): Promise<void> {
  database.exec(`
    CREATE TABLE IF NOT EXISTS __drizzle_migrations (
      name TEXT PRIMARY KEY NOT NULL,
      hash TEXT NOT NULL,
      applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
  `);
  const files = (await readdir(folder)).filter((name) => name.endsWith(".sql")).sort();
  const applied = database.prepare("SELECT hash FROM __drizzle_migrations WHERE name = ?");
  const remember = database.prepare("INSERT INTO __drizzle_migrations (name, hash) VALUES (?, ?)");

  for (const name of files) {
    const sql = await readFile(`${folder}/${name}`, "utf8");
    const hash = createHash("sha256").update(sql).digest("hex");
    const previous = applied.get(name) as { hash: string } | undefined;
    if (previous?.hash === hash) continue;
    if (previous) throw new Error(`migration history was edited: ${name}`);
    database.exec("BEGIN IMMEDIATE");
    try {
      database.exec(sql);
      remember.run(name, hash);
      database.exec("COMMIT");
    } catch (error) {
      database.exec("ROLLBACK");
      throw error;
    }
  }
}
