import { eq } from "drizzle-orm";
import { sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";

export const settings = sqliteTable("books_settings", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
});

/** One key/value pair of a workspace's settings. */
export type Setting = typeof settings.$inferSelect;

/** A workspace's settings: provider credentials, channel offsets, migration markers. */
export class SettingsBook {
  constructor(private readonly db: BusDb) {}

  get(key: string): string | null {
    return this.db.select().from(settings).where(eq(settings.key, key)).get()?.value ?? null;
  }
  set(key: string, value: string): void {
    this.db.insert(settings).values({ key, value })
      .onConflictDoUpdate({ target: settings.key, set: { value } })
      .run();
  }
}
