import { and, eq, sql } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";
import { transaction } from "../sqlite.js";
import { settings } from "./settingsBook.js";

/** One MAGI's durable working notes, not scheduled tasks or execution jobs. */
export const actionItems = sqliteTable("books_action_items", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  content: text("content").notNull(),
  version: integer("version").notNull().default(1),
  archived: integer("archived", { mode: "boolean" }).notNull().default(false),
  created_at: text("created_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
  updated_at: text("updated_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
});

export type ActionItem = typeof actionItems.$inferSelect;

export class ActionItemBook {
  constructor(private readonly db: BusDb) {}

  get(id: number): ActionItem | null {
    return this.db.select().from(actionItems).where(eq(actionItems.id, id)).get() ?? null;
  }

  list(includeArchived = false): ActionItem[] {
    return this.db.select().from(actionItems)
      .where(includeArchived ? undefined : eq(actionItems.archived, false))
      .orderBy(actionItems.id).all();
  }

  save(input: { id?: number; content?: string; archived?: boolean; expectedVersion?: number }): ActionItem {
    this.validateVersion(input.expectedVersion);
    if (input.id === undefined && input.expectedVersion !== undefined) throw new Error("expectedVersion is only valid when updating an action item");
    if (input.content !== undefined && !input.content.trim()) throw new Error("action item content is required");
    if (input.id === undefined) {
      if (input.content === undefined) throw new Error("action item content is required");
      return this.db.insert(actionItems).values({ content: input.content.trim(), archived: input.archived ?? false }).returning().get();
    }
    const current = this.get(input.id);
    if (!current) throw new Error(`action item ${input.id} not found`);
    const updated = this.db.update(actionItems).set({
      content: input.content?.trim() ?? current.content,
      archived: input.archived ?? current.archived,
      updated_at: sql`(CURRENT_TIMESTAMP)`,
      version: sql`${actionItems.version} + 1`,
    }).where(and(eq(actionItems.id, input.id), input.expectedVersion === undefined ? undefined : eq(actionItems.version, input.expectedVersion))).returning().get();
    if (!updated) throw new Error(`action item ${input.id} changed; read its current version before retrying`);
    return updated;
  }

  /** A preset is introduced only once, including after the user deletes its note.
   * The marker and note commit together; failed validation cannot consume the key.
   * Returns the new note, or null when this preset was already introduced.
   */
  seedOnce(key: string, content: string): ActionItem | null {
    if (!key.trim()) throw new Error("action item seed key is required");
    return transaction(this.db.$client, () => {
      const marker = this.db.insert(settings).values({ key: `action_items.seed.${key.trim()}`, value: "1" })
        .onConflictDoNothing().returning({ key: settings.key }).get();
      return marker ? this.save({ content }) : null;
    }, "immediate");
  }

  delete(id: number, expectedVersion?: number): boolean {
    this.validateVersion(expectedVersion);
    const deleted = this.db.delete(actionItems)
      .where(and(eq(actionItems.id, id), expectedVersion === undefined ? undefined : eq(actionItems.version, expectedVersion)))
      .returning({ id: actionItems.id }).get();
    if (!deleted && expectedVersion !== undefined && this.get(id)) {
      throw new Error(`action item ${id} changed; read its current version before retrying`);
    }
    return deleted !== undefined;
  }

  private validateVersion(version: number | undefined): void {
    if (version !== undefined && (!Number.isSafeInteger(version) || version < 1)) throw new Error("expectedVersion must be a positive integer");
  }
}
