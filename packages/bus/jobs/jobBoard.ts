import { and, eq, inArray, sql } from "drizzle-orm";
import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";
import type { CallLLMJob, CallLLMResult } from "./callLlm.js";
import type { ChangeMcpServerNotify } from "./changeMcpServer.js";
import type { ChangeProviderNotify } from "./changeProvider.js";
import type { MessageDeliveryJob } from "./messageDelivery.js";
import type { ManageWorkerNotify } from "./manageWorker.js";
import type { RunToolJob, RunToolResult } from "./runTool.js";

/**
 * The board contract: which job types exist, what each carries in and out, and
 * what a claimed job looks like.
 *
 * Each payload lives in its own file beside this one (`messageDelivery.ts`,
 * `callLlm.ts`, …), so a reader opens the job they care about instead of
 * scanning one long list. This file only wires them into the board.
 */

/** Job type -> what its publisher hands to the board. */
export type JobInput = {
  MessageDeliveryJob: MessageDeliveryJob;
  CallLLMJob: CallLLMJob;
  RunToolJob: RunToolJob;
  ChangeProviderNotify: ChangeProviderNotify;
  ChangeMcpServerNotify: ChangeMcpServerNotify;
  ManageWorkerNotify: ManageWorkerNotify;
};

/** Job type -> what the worker writes back. Empty where the job only acts. */
export type JobOutput = {
  MessageDeliveryJob: Record<string, never>;
  CallLLMJob: CallLLMResult;
  RunToolJob: RunToolResult;
  ChangeProviderNotify: Record<string, never>;
  ChangeMcpServerNotify: Record<string, never>;
  ManageWorkerNotify: { running: string[] };
};

export type JobType = keyof JobInput;
export type JobStatus = "pending" | "claimed" | "completed" | "failed";
export type Job<K extends JobType> = { id: number; type: K; input: JobInput[K]; status: JobStatus; worker?: string };
export type JobResult<K extends JobType> = { id: number; status: "completed" | "failed"; output?: JobOutput[K]; error?: string };

export const jobs = sqliteTable("jobs", {
  id: integer("id").primaryKey(),
  type: text("type").$type<JobType>().notNull(),
  publisher: text("publisher").notNull(),
  status: text("status").$type<JobStatus>().notNull().default("pending"),
  worker: text("worker"),
  input: text("input").notNull(),
  output: text("output"),
  error: text("error"),
  created_at: text("created_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
  updated_at: text("updated_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
}, (table) => [index("jobs_claim").on(table.type, table.status, table.id)]);

export class JobBoard<K extends JobType> {
  constructor(private readonly db: BusDb, readonly type: K) {}

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
        // The SQL result is the portable claim signal: ask for the row the update won.
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
