import { and, eq, inArray, sql } from "drizzle-orm";
import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";
import { transaction } from "../sqlite.js";
import type { AspCommandJob, AspCommandResult } from "./aspCommand.js";
import type { CallLLMJob, CallLLMResult } from "./callLlm.js";
import type { CardJob, CardResult } from "./card.js";
import type { ChangeMcpServerNotify } from "./changeMcpServer.js";
import type { ChangeProviderNotify } from "./changeProvider.js";
import type { ConfigureJob, ConfigureResult } from "./configure.js";
import type { InboundSecretJob, InboundSecretResult } from "./inboundSecret.js";
import type { MessageDeliveryJob } from "./messageDelivery.js";
import type { ManageWorkerNotify } from "./manageWorker.js";
import type { ReplaceSelfJob, ReplaceSelfResult } from "./replaceSelf.js";
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
  ConfigureJob: ConfigureJob;
  CardJob: CardJob;
  ReplaceSelfJob: ReplaceSelfJob;
  AspCommandJob: AspCommandJob;
  InboundSecretJob: InboundSecretJob;
};

/** Job type -> what the worker writes back. Empty where the job only acts. */
export type JobOutput = {
  MessageDeliveryJob: Record<string, never>;
  CallLLMJob: CallLLMResult;
  RunToolJob: RunToolResult;
  ChangeProviderNotify: Record<string, never>;
  ChangeMcpServerNotify: Record<string, never>;
  ManageWorkerNotify: { running: string[] };
  ConfigureJob: ConfigureResult;
  CardJob: CardResult;
  ReplaceSelfJob: ReplaceSelfResult;
  AspCommandJob: AspCommandResult;
  InboundSecretJob: InboundSecretResult;
};

export type JobType = keyof JobInput;
export type JobStatus = "pending" | "claimed" | "completed" | "failed";
export type Job<K extends JobType> = { id: number; type: K; input: JobInput[K]; status: JobStatus; worker?: string };
export type JobResult<K extends JobType> = { id: number; status: "completed" | "failed"; output?: JobOutput[K]; error?: string };

export const jobs = sqliteTable("jobs", {
  id: integer("id").primaryKey(),
  type: text("type").$type<JobType>().notNull(),
  delivery_key: text("delivery_key").unique(),
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

  /** Cross-database reconciliation may repeat this after a crash or lost ACK. */
  publishOnce(key: string, input: JobInput[K], publisher: string): number {
    this.db.insert(jobs).values({ type: this.type, publisher, input: JSON.stringify(input), delivery_key: key }).onConflictDoNothing().run();
    const row = this.db.select().from(jobs).where(eq(jobs.delivery_key, key)).get();
    if (!row || row.type !== this.type) throw new Error("delivery key collision");
    return row.id;
  }

  claim(worker: string, predicate?: (input: JobInput[K]) => boolean): Job<K> | null {
    return transaction(this.db.$client, () => {
      const rows = this.db.select({ id: jobs.id, input: jobs.input }).from(jobs)
        .where(and(eq(jobs.type, this.type), eq(jobs.status, "pending")))
        .orderBy(jobs.id)
        .limit(100)
        .all();
      for (const row of rows) {
        const input = JSON.parse(row.input) as JobInput[K];
        if (predicate && !predicate(input)) continue;
        // The SQL result is the portable claim signal: ask for the row the update won.
        const claimed = this.db.update(jobs)
          .set({ status: "claimed", worker, updated_at: sql`(CURRENT_TIMESTAMP)` })
          .where(and(eq(jobs.id, row.id), eq(jobs.status, "pending")))
          .returning({ id: jobs.id })
          .get();
        if (claimed) return { id: row.id, type: this.type, input, status: "claimed" as const, worker };
      }
      return null;
    }, "immediate");
  }

  /** Claim one pending job by id. A different pending job is left alone. */
  claimId(worker: string, id: number): Job<K> | null {
    return transaction(this.db.$client, () => {
      const row = this.db.select({ id: jobs.id, input: jobs.input }).from(jobs)
        .where(and(eq(jobs.id, id), eq(jobs.type, this.type), eq(jobs.status, "pending")))
        .get();
      if (!row) return null;
      const claimed = this.db.update(jobs)
        .set({ status: "claimed", worker, updated_at: sql`(CURRENT_TIMESTAMP)` })
        .where(and(eq(jobs.id, row.id), eq(jobs.status, "pending")))
        .returning({ id: jobs.id })
        .get();
      if (!claimed) return null;
      return { id: row.id, type: this.type, input: JSON.parse(row.input) as JobInput[K], status: "claimed" as const, worker };
    }, "immediate");
  }

  /** Fail a job that is still open. A claimed job has no claim left to take. */
  fail(id: number, error: string): boolean {
    const failed = this.db.update(jobs)
      .set({ status: "failed", output: null, error, updated_at: sql`(CURRENT_TIMESTAMP)` })
      .where(and(eq(jobs.id, id), eq(jobs.type, this.type), inArray(jobs.status, ["pending", "claimed"])))
      .returning({ id: jobs.id })
      .get();
    return failed !== undefined;
  }

  /** Requeue a claimed delivery or editable form after a transient failure. */
  release(worker: string, id: number): boolean {
    return this.db.update(jobs).set({ status: "pending", worker: null, updated_at: sql`(CURRENT_TIMESTAMP)` })
      .where(and(eq(jobs.id, id), eq(jobs.type, this.type), eq(jobs.status, "claimed"), eq(jobs.worker, worker)))
      .returning({ id: jobs.id }).get() !== undefined;
  }

  /** A failure that was only going to be logged. It is a JobResult, and it is not claimable. */
  seal(publisher: string, input: JobInput[K], error: string): number {
    return this.db.insert(jobs).values({
      type: this.type,
      publisher,
      status: "failed",
      input: JSON.stringify(input),
      error,
    }).returning({ id: jobs.id }).get().id;
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

  /** Input of a job in any status. Replace failures read chat_id from this, not a global setting. */
  peekInput(id: number): JobInput[K] | null {
    const row = this.db.select({ input: jobs.input }).from(jobs)
      .where(and(eq(jobs.id, id), eq(jobs.type, this.type)))
      .get();
    return row ? JSON.parse(row.input) as JobInput[K] : null;
  }
}
