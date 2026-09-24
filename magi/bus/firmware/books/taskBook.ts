import { eq } from "drizzle-orm";
import type { BooksDb } from "../database.js";
import { tasks } from "../schema.js";

export type Task = typeof tasks.$inferSelect;

export class TaskBook {
  constructor(private readonly db: BooksDb) {}

  list(enabledOnly = false): Task[] {
    return this.db.select().from(tasks)
      .where(enabledOnly ? eq(tasks.enabled, true) : undefined)
      .orderBy(tasks.id)
      .all();
  }

  get(id: number): Task | null {
    return this.db.select().from(tasks).where(eq(tasks.id, id)).get() ?? null;
  }

  save(input: { name: string; prompt: string; cron: string; conversation_id: number; enabled?: boolean }): Task {
    if (!input.name.trim() || input.name.length > 120) throw new Error("task name must contain 1..120 characters");
    if (!input.prompt.trim()) throw new Error("task prompt is required");
    const fields = {
      name: input.name.trim(),
      prompt: input.prompt.trim(),
      cron: input.cron,
      conversation_id: input.conversation_id,
      enabled: input.enabled !== false,
    };
    return this.db.insert(tasks).values(fields)
      .onConflictDoUpdate({ target: tasks.name, set: { prompt: fields.prompt, cron: fields.cron, enabled: fields.enabled } })
      .returning().get();
  }

  markFired(id: number, minute: string): void {
    this.db.update(tasks).set({ last_fired_minute: minute }).where(eq(tasks.id, id)).run();
  }
}
