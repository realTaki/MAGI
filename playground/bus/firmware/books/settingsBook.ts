import type { Sqlite } from "../../base/sqlite.js";

/** Firmware-private persistent key/value Book. */
export class SettingsBook {
  constructor(private readonly sqlite: Sqlite) {}

  get(key: string): string | null {
    const row = this.sqlite.database
      .prepare("SELECT value FROM books_settings WHERE key = ?")
      .get(key) as { value: string } | undefined;
    return row?.value ?? null;
  }

  boostDefaults(namespace: string, values: Record<string, string>): void {
    const insert = this.sqlite.database.prepare(
      "INSERT OR IGNORE INTO books_settings (key, value) VALUES (?, ?)",
    );
    this.sqlite.transaction(() => {
      for (const [key, value] of Object.entries(values)) insert.run(`${namespace}.${key}`, value);
    });
  }
}
