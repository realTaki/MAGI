import { sql } from "drizzle-orm";
import { check, index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

const timestamps = {
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
};

export const settings = sqliteTable("books_settings", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
});

export const getSettingJobs = sqliteTable(
  "jobs_get_setting",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    publisher: text("publisher").notNull(),
    status: text("status").notNull(),
    error: text("error"),
    key: text("key").notNull(),
    value: text("value"),
    ...timestamps,
  },
  (table) => [check("jobs_get_setting_status", sql`${table.status} IN ('completed', 'failed')`)],
);

export const callLLMJobs = sqliteTable(
  "jobs_call_llm",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    publisher: text("publisher").notNull(),
    worker: text("worker"),
    status: text("status").notNull(),
    error: text("error"),
    messages: text("messages").notNull(),
    contactId: integer("contact_id"),
    maxTokens: integer("max_tokens").notNull().default(1024),
    tools: text("tools"),
    text: text("text").notNull().default(""),
    thinking: text("thinking"),
    toolUses: text("tool_uses"),
    rawBlocks: text("raw_blocks"),
    finishReason: text("finish_reason"),
    model: text("model").notNull().default(""),
    errorCode: text("error_code"),
    ...timestamps,
  },
  (table) => [
    check(
      "jobs_call_llm_status",
      sql`${table.status} IN ('pending', 'claimed', 'completed', 'failed')`,
    ),
    index("jobs_call_llm_claim_idx").on(table.status, table.id),
  ],
);
