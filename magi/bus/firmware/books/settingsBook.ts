import type { Database } from "bun:sqlite";

/** One key/value pair of a workspace's settings. */
export type Setting = { key: string; value: string };

/** A workspace's settings: provider credentials, channel offsets, migration markers. */
export class SettingsBook {
  constructor(private readonly db: Database) {}

  get(key: string): string | null {
    const row = this.db.prepare("SELECT value FROM books_settings WHERE key = ?").get(key) as { value: string } | undefined;
    return row?.value ?? null;
  }
  set(key: string, value: string): void {
    this.db.prepare("INSERT INTO books_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").run(key, value);
  }
  all(): Setting[] {
    return this.db.prepare("SELECT key, value FROM books_settings ORDER BY key").all() as Setting[];
  }
}
