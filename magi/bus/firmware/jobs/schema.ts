import { sql } from "drizzle-orm";
import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { JobStatus, JobType } from "./types.js";

/** The job queue of one MAGI workspace (`<workspace>/logs/magi.db`). */
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
