import { and, eq, sql } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";

export const memories = sqliteTable("books_memories", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  topic: text("topic").notNull(),
  detail: text("detail").notNull(),
  kind: text("kind").$type<"temporary" | "short_term" | "long_term">().notNull().default("temporary"),
  archived: integer("archived", { mode: "boolean" }).notNull().default(false),
  version: integer("version").notNull().default(1),
  created_at: text("created_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
});

export type Memory = typeof memories.$inferSelect;
export type MemoryKind = Memory["kind"];

export class MemoryBook {
  constructor(private readonly db: BusDb) {}

  list(includeArchived = false): Memory[] {
    return this.db.select().from(memories)
      .where(includeArchived ? undefined : eq(memories.archived, false))
      .orderBy(memories.id)
      .all();
  }

  get(id: number): Memory | null {
    return this.db.select().from(memories).where(eq(memories.id, id)).get() ?? null;
  }

  save(input: { id?: number; topic?: string; detail?: string; kind?: MemoryKind; archived?: boolean; expectedVersion?: number }): Memory {
    if (input.id !== undefined && (!Number.isSafeInteger(input.id) || input.id < 1)) throw new Error("memory id must be a positive integer");
    if (input.expectedVersion !== undefined && (!Number.isSafeInteger(input.expectedVersion) || input.expectedVersion < 1)) throw new Error("expectedVersion must be a positive integer");
    if (input.topic !== undefined && (typeof input.topic !== "string" || !input.topic.trim())) throw new Error("topic must be a nonempty string");
    if (input.detail !== undefined && (typeof input.detail !== "string" || !input.detail.trim())) throw new Error("detail must be a nonempty string");
    if (input.kind !== undefined && !["temporary", "short_term", "long_term"].includes(input.kind)) throw new Error("invalid memory kind");
    if (input.archived !== undefined && typeof input.archived !== "boolean") throw new Error("archived must be a boolean");
    if (input.id === undefined) {
      if (input.expectedVersion !== undefined) throw new Error("expectedVersion is only valid when updating a memory");
      if (!input.topic?.trim() || !input.detail?.trim()) throw new Error("topic and detail are required");
      return this.db.insert(memories)
        .values({ topic: input.topic.trim(), detail: input.detail, kind: input.kind ?? "temporary", archived: input.archived === true })
        .returning().get();
    }
    if (input.expectedVersion === undefined) throw new Error("expectedVersion is required when updating a memory; read its current version first");
    // Patch only supplied fields; the version predicate is the atomic read/write
    // boundary shared by foreground, subconscious, and other database connections.
    const updated = this.db.update(memories)
      .set({
        ...(input.topic === undefined ? {} : { topic: input.topic.trim() }),
        ...(input.detail === undefined ? {} : { detail: input.detail }),
        ...(input.kind === undefined ? {} : { kind: input.kind }),
        ...(input.archived === undefined ? {} : { archived: input.archived }),
        version: sql`${memories.version} + 1`,
      })
      .where(and(eq(memories.id, input.id), eq(memories.version, input.expectedVersion)))
      .returning().get();
    if (!updated) {
      if (!this.get(input.id)) throw new Error(`memory ${input.id} not found`);
      throw new Error(`memory ${input.id} changed; read its current version before retrying`);
    }
    return updated;
  }

  delete(id: number): boolean {
    // Ask for the deleted row so this works consistently across SQLite drivers.
    return this.db.delete(memories).where(eq(memories.id, id)).returning({ id: memories.id }).get() !== undefined;
  }
}
