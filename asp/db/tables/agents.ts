/**
 * Registered agents: one row per handle.
 *
 * The record holds the token, the display name, and the inbound policy, so the
 * row shape is the agent itself (`agentFromJson` in `store.ts`).
 */

import { sqliteTable, text } from "drizzle-orm/sqlite-core";

export const aspAgents = sqliteTable("asp_agents", {
  handle: text("handle").primaryKey(),
  record_json: text("record_json", { mode: "json" }).$type<unknown>().notNull(),
});
