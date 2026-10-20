import { and, eq, ne, or, isNull, sql } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";
import { transaction } from "../sqlite.js";
import { settings } from "./settingsBook.js";
import { chats } from "./chatBook.js";

export const tasks = sqliteTable("books_tasks", {
  id: integer("id").primaryKey(),
  name: text("name").notNull(),
  prompt: text("prompt").notNull(),
  source: text("source").$type<"user" | "subconscious">().notNull().default("user"),
  enabled: integer("enabled", { mode: "boolean" }).notNull().default(true),
  cron: text("cron").notNull(),
  chat_id: integer("chat_id").notNull().references(() => chats.id, { onDelete: "cascade" }),
  last_fired_minute: text("last_fired_minute"),
  run_at: text("run_at"),
});
export type Task = typeof tasks.$inferSelect;
type TaskInput = { name: string; prompt: string; cron: string; chat_id: number; enabled?: boolean; run_at?: string | null };

export class TaskBook {
  constructor(private readonly db: BusDb) {}
  list(enabledOnly = false): Task[] {
    return this.db.select().from(tasks).where(enabledOnly ? eq(tasks.enabled, true) : undefined).orderBy(tasks.id).all();
  }
  get(id: number): Task | null { return this.db.select().from(tasks).where(eq(tasks.id, id)).get() ?? null; }
  create(input: TaskInput): Task {
    this.validate(input);
    const fields = { ...input, name: input.name.trim(), prompt: input.prompt.trim(), enabled: input.enabled !== false, run_at: input.run_at ?? null };
    return this.db.insert(tasks).values(fields).returning().get();
  }
  update(id: number, patch: Partial<TaskInput>): Task {
    const old = this.get(id);
    if (!old) throw new Error(`unknown task ${id}`);
    const fields = { ...old, ...patch };
    this.validate(fields);
    this.validateRearm(old, fields);
    return this.db.update(tasks).set({ ...patch, ...(patch.name === undefined ? {} : { name: patch.name.trim() }), ...(patch.prompt === undefined ? {} : { prompt: patch.prompt.trim() }) }).where(eq(tasks.id, id)).returning().get();
  }
  delete(id: number): boolean { return this.db.delete(tasks).where(eq(tasks.id, id)).run().changes > 0; }
  /** Marker survives deletion and edits. A restart never re-enables an opted-out preset. */
  seedOnce(key: string, input: TaskInput, now = new Date()): void {
    transaction(this.db.$client, () => {
      const marker = `subconscious.task.seed.${key}`;
      if (this.db.select().from(settings).where(eq(settings.key, marker)).get()) return;
      this.validate(input);
      this.db.insert(tasks).values({ ...input, source: "subconscious", last_fired_minute: now.toISOString().slice(0, 16) }).run();
      this.db.insert(settings).values({ key: marker, value: "1" }).run();
    }, "immediate");
  }
  /** Atomic compare-and-set prevents two scanners firing the same minute/one-shot. */
  claimFire(id: number, minute: string): boolean {
    return this.db.update(tasks).set({ last_fired_minute: minute, enabled: sql`CASE WHEN ${tasks.run_at} IS NULL THEN 1 ELSE 0 END` })
      .where(and(eq(tasks.id, id), eq(tasks.enabled, true), or(isNull(tasks.last_fired_minute), ne(tasks.last_fired_minute, minute))))
      .run().changes > 0;
  }
  /** Commit the fire marker and the existing delivery journal together, before waking jobs.db. */
  fire<T>(id: number, minute: string, persist: () => T): T | null {
    return transaction(this.db.$client, () => this.claimFire(id, minute) ? persist() : null, "immediate");
  }
  markFired(id: number, minute: string): void { this.db.update(tasks).set({ last_fired_minute: minute }).where(eq(tasks.id, id)).run(); }
  private validateRearm(old: Task | undefined, next: TaskInput): void {
    if (old?.run_at && old.last_fired_minute && !old.enabled && next.enabled !== false && next.run_at != null && Date.parse(next.run_at) <= Date.now()) {
      throw new Error("reschedule a completed one-time task to a future run_at before enabling it");
    }
  }
  private validate(input: TaskInput): void {
    if (!input.name.trim() || input.name.length > 120) throw new Error("task name must contain 1..120 characters");
    if (!input.prompt.trim()) throw new Error("task prompt is required");
    if (input.run_at != null && (!Number.isFinite(Date.parse(input.run_at)) || !/(Z|[+-]\d\d:\d\d)$/.test(input.run_at))) throw new Error("run_at must be an ISO timestamp with timezone");
  }
}
