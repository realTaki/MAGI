import { eq } from "drizzle-orm";
import type { BooksDb } from "../database.js";
import { settings } from "../schema.js";

/** One key/value pair of a workspace's settings. */
export type Setting = typeof settings.$inferSelect;

/** A workspace's settings: provider credentials, channel offsets, migration markers. */
export class SettingsBook {
  constructor(private readonly db: BooksDb) {}

  get(key: string): string | null {
    return this.db.select().from(settings).where(eq(settings.key, key)).get()?.value ?? null;
  }
  set(key: string, value: string): void {
    this.db.insert(settings).values({ key, value })
      .onConflictDoUpdate({ target: settings.key, set: { value } })
      .run();
  }
  all(): Setting[] {
    return this.db.select().from(settings).orderBy(settings.key).all();
  }
}
