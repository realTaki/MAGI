import type { Database } from "bun:sqlite";
import type { Job, JobInput, JobOutput, JobResult, JobType } from "./types.js";

type Row = { id: number; type: JobType; input: string; status: Job<"ChatNotify">["status"]; worker: string | null; output: string | null; error: string | null };

export class JobBoard<K extends JobType> {
  constructor(private readonly db: Database, readonly type: K) {}

  publish(input: JobInput[K], publisher: string): number {
    const result = this.db.prepare("INSERT INTO jobs (type, publisher, input) VALUES (?, ?, ?)")
      .run(this.type, publisher, JSON.stringify(input));
    return Number(result.lastInsertRowid);
  }

  claim(worker: string, predicate?: (input: JobInput[K]) => boolean): Job<K> | null {
    return this.db.transaction(() => {
      const rows = this.db.prepare("SELECT id, type, input, status, worker, output, error FROM jobs WHERE type = ? AND status = 'pending' ORDER BY id LIMIT 100")
        .all(this.type) as Row[];
      for (const row of rows) {
        const input = JSON.parse(row.input) as JobInput[K];
        if (predicate && !predicate(input)) continue;
        const changed = this.db.prepare("UPDATE jobs SET status = 'claimed', worker = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'")
          .run(worker, row.id);
        if (changed.changes === 1) return { id: row.id, type: this.type, input, status: "claimed" as const, worker };
      }
      return null;
    }).immediate();
  }

  submit(worker: string, id: number, outcome: { output?: JobOutput[K]; error?: string }): boolean {
    const changed = this.db.prepare("UPDATE jobs SET status = ?, output = ?, error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND type = ? AND status = 'claimed' AND worker = ?")
      .run(outcome.error === undefined ? "completed" : "failed", outcome.output === undefined ? null : JSON.stringify(outcome.output), outcome.error ?? null, id, this.type, worker);
    return changed.changes === 1;
  }

  result(id: number): JobResult<K> | null {
    const row = this.db.prepare("SELECT id, type, input, status, worker, output, error FROM jobs WHERE id = ? AND type = ? AND status IN ('completed', 'failed')")
      .get(id, this.type) as Row | undefined;
    if (!row) return null;
    return {
      id: row.id,
      status: row.status as "completed" | "failed",
      output: row.output ? JSON.parse(row.output) as JobOutput[K] : undefined,
      error: row.error ?? undefined,
    };
  }
}
