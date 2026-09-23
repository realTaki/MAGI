import type { Database } from "bun:sqlite";

export type Task = {
  id: number; name: string; prompt: string; source: "user" | "proactive";
  enabled: number; cron: string; conversation_id: number; last_fired_minute: string | null;
};

export class TaskBook {
  constructor(private readonly db: Database) {}

  list(enabledOnly = false): Task[] {
    return this.db.prepare(`SELECT * FROM books_tasks ${enabledOnly ? "WHERE enabled = 1" : ""} ORDER BY id`).all() as Task[];
  }

  get(id: number): Task | null {
    return (this.db.prepare("SELECT * FROM books_tasks WHERE id = ?").get(id) as Task | undefined) ?? null;
  }

  save(input: { name: string; prompt: string; cron: string; conversation_id: number; enabled?: boolean }): Task {
    if (!input.name.trim() || input.name.length > 120) throw new Error("task name must contain 1..120 characters");
    if (!input.prompt.trim()) throw new Error("task prompt is required");
    this.db.prepare(`INSERT INTO books_tasks (name, prompt, cron, conversation_id, enabled)
      VALUES (?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET prompt = excluded.prompt, cron = excluded.cron, enabled = excluded.enabled`)
      .run(input.name.trim(), input.prompt.trim(), input.cron, input.conversation_id, input.enabled === false ? 0 : 1);
    return this.db.prepare("SELECT * FROM books_tasks WHERE name = ?").get(input.name.trim()) as Task;
  }

  markFired(id: number, minute: string): void {
    this.db.prepare("UPDATE books_tasks SET last_fired_minute = ? WHERE id = ?").run(minute, id);
  }
}
