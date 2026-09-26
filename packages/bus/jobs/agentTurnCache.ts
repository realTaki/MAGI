import { and, asc, eq, index, integer, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";
import type { LLMTool } from "../books/toolBook.js";
import type { LLMMessage, LLMRequest } from "./callLlm.js";
import { jobs } from "./jobBoard.js";

export const agentTurnsCache = sqliteTable("agent_turns_cache", {
  id: integer("id").primaryKey(),
  /** The MessageDeliveryJob id is the stable public identity of a turn. */
  turn_id: integer("turn_id").notNull().references(() => jobs.id, { onDelete: "cascade" }),
  previous_block_id: integer("previous_block_id").references(() => agentTurnsCache.id, { onDelete: "cascade" }),
  sequence: integer("sequence").notNull(),
  kind: text("kind").$type<"context" | "assistant" | "tool_result">().notNull(),
  /** The producer CallLLMJob, when this is an assistant response. */
  job_id: integer("job_id").references(() => jobs.id, { onDelete: "cascade" }),
  payload: text("payload").notNull(),
  created_at: text("created_at").notNull().default("CURRENT_TIMESTAMP"),
}, (table) => [
  uniqueIndex("agent_turns_cache_sequence").on(table.turn_id, table.sequence),
  uniqueIndex("agent_turns_cache_job").on(table.job_id),
  index("agent_turns_cache_turn").on(table.turn_id, table.id),
]);

export type AgentTurnContext = LLMRequest;
type BlockPayload = { context: AgentTurnContext } | { message: LLMMessage };

/**
 * Hot shared contexts plus a cold append-only journal.  The journal is intentionally
 * operational data in jobs.db: completed turns can be inspected or cleaned without
 * becoming durable Agent memory.
 */
export class AgentTurnCache {
  private readonly hot = new Map<number, AgentTurnContext>();

  constructor(private readonly db: BusDb) {}

  ensure(turn_id: number, context: AgentTurnContext): AgentTurnContext {
    const existing = this.getOrNull(turn_id);
    if (existing) return existing;
    this.db.insert(agentTurnsCache).values({
      turn_id, previous_block_id: null, sequence: 0, kind: "context", job_id: null,
      payload: JSON.stringify({ context }),
    }).run();
    this.hot.set(turn_id, context);
    return context;
  }

  get(turn_id: number): AgentTurnContext {
    return this.getOrNull(turn_id) ?? (() => { throw new Error(`agent turn ${turn_id} does not exist`); })();
  }

  assistant(turn_id: number, job_id: number): LLMMessage {
    const row = this.db.select({ payload: agentTurnsCache.payload }).from(agentTurnsCache)
      .where(and(eq(agentTurnsCache.turn_id, turn_id), eq(agentTurnsCache.job_id, job_id), eq(agentTurnsCache.kind, "assistant")))
      .get();
    if (!row) throw new Error(`agent turn ${turn_id} has no response for CallLLMJob ${job_id}`);
    return (JSON.parse(row.payload) as { message: LLMMessage }).message;
  }

  appendAssistant(turn_id: number, job_id: number, message: LLMMessage): LLMMessage {
    const existing = this.db.select({ payload: agentTurnsCache.payload }).from(agentTurnsCache)
      .where(and(eq(agentTurnsCache.turn_id, turn_id), eq(agentTurnsCache.job_id, job_id), eq(agentTurnsCache.kind, "assistant")))
      .get();
    if (existing) return (JSON.parse(existing.payload) as { message: LLMMessage }).message;
    this.append(turn_id, "assistant", { message }, job_id);
    return message;
  }

  appendToolResult(turn_id: number, message: LLMMessage): void {
    this.append(turn_id, "tool_result", { message });
  }

  evict(turn_id: number): void { this.hot.delete(turn_id); }

  private getOrNull(turn_id: number): AgentTurnContext | null {
    const cached = this.hot.get(turn_id);
    if (cached) return cached;
    const blocks = this.db.select({ kind: agentTurnsCache.kind, payload: agentTurnsCache.payload }).from(agentTurnsCache)
      .where(eq(agentTurnsCache.turn_id, turn_id)).orderBy(asc(agentTurnsCache.sequence)).all();
    if (!blocks.length) return null;
    const root = blocks.shift();
    if (!root || root.kind !== "context") throw new Error(`agent turn ${turn_id} has no context root`);
    const context = (JSON.parse(root.payload) as BlockPayload & { context: AgentTurnContext }).context;
    for (const block of blocks) {
      const payload = JSON.parse(block.payload) as { message: LLMMessage };
      context.messages.push(payload.message);
    }
    this.hot.set(turn_id, context);
    return context;
  }

  private append(turn_id: number, kind: "assistant" | "tool_result", payload: { message: LLMMessage }, job_id: number | null = null): void {
    const context = this.get(turn_id);
    this.db.transaction((tx) => {
      const previous = tx.select({ id: agentTurnsCache.id, sequence: agentTurnsCache.sequence }).from(agentTurnsCache)
        .where(eq(agentTurnsCache.turn_id, turn_id)).orderBy(agentTurnsCache.sequence).all().at(-1);
      if (!previous) throw new Error(`agent turn ${turn_id} has no context root`);
      tx.insert(agentTurnsCache).values({
        turn_id, previous_block_id: previous.id, sequence: previous.sequence + 1, kind, job_id,
        payload: JSON.stringify(payload),
      }).run();
    }, { behavior: "immediate" });
    // The durable append happened first; hot contexts only avoid replaying it from disk.
    context.messages.push(payload.message);
  }
}
