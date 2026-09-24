import { and, eq, inArray, sql } from "drizzle-orm";
import type { JobsDb } from "../database.js";
import { jobs } from "./schema.js";
import type { Job, JobInput, JobOutput, JobResult, JobType } from "./types.js";

export class JobBoard<K extends JobType> {
  constructor(private readonly db: JobsDb, readonly type: K) {}

  publish(input: JobInput[K], publisher: string): number {
    return this.db.insert(jobs)
      .values({ type: this.type, publisher, input: JSON.stringify(input) })
      .returning({ id: jobs.id })
      .get().id;
  }

  claim(worker: string, predicate?: (input: JobInput[K]) => boolean): Job<K> | null {
    return this.db.transaction((tx) => {
      const rows = tx.select({ id: jobs.id, input: jobs.input }).from(jobs)
        .where(and(eq(jobs.type, this.type), eq(jobs.status, "pending")))
        .orderBy(jobs.id)
        .limit(100)
        .all();
      for (const row of rows) {
        const input = JSON.parse(row.input) as JobInput[K];
        if (predicate && !predicate(input)) continue;
        // bun:sqlite reports no row count, so claim by asking for the row back.
        const claimed = tx.update(jobs)
          .set({ status: "claimed", worker, updated_at: sql`(CURRENT_TIMESTAMP)` })
          .where(and(eq(jobs.id, row.id), eq(jobs.status, "pending")))
          .returning({ id: jobs.id })
          .get();
        if (claimed) return { id: row.id, type: this.type, input, status: "claimed" as const, worker };
      }
      return null;
    }, { behavior: "immediate" });
  }

  submit(worker: string, id: number, outcome: { output?: JobOutput[K]; error?: string }): boolean {
    const completed = this.db.update(jobs)
      .set({
        status: outcome.error === undefined ? "completed" : "failed",
        output: outcome.output === undefined ? null : JSON.stringify(outcome.output),
        error: outcome.error ?? null,
        updated_at: sql`(CURRENT_TIMESTAMP)`,
      })
      .where(and(eq(jobs.id, id), eq(jobs.type, this.type), eq(jobs.status, "claimed"), eq(jobs.worker, worker)))
      .returning({ id: jobs.id })
      .get();
    return completed !== undefined;
  }

  result(id: number): JobResult<K> | null {
    const row = this.db.select({ id: jobs.id, status: jobs.status, output: jobs.output, error: jobs.error }).from(jobs)
      .where(and(eq(jobs.id, id), eq(jobs.type, this.type), inArray(jobs.status, ["completed", "failed"])))
      .get();
    if (!row) return null;
    return {
      id: row.id,
      status: row.status as "completed" | "failed",
      output: row.output ? JSON.parse(row.output) as JobOutput[K] : undefined,
      error: row.error ?? undefined,
    };
  }
}
