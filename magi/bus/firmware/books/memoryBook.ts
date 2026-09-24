import { eq } from "drizzle-orm";
import type { BooksDb } from "../database.js";
import { memories } from "../schema.js";

export type Memory = typeof memories.$inferSelect;
export type MemoryKind = Memory["kind"];

export class MemoryBook {
  constructor(private readonly db: BooksDb) {}

  list(includeArchived = false): Memory[] {
    return this.db.select().from(memories)
      .where(includeArchived ? undefined : eq(memories.archived, false))
      .orderBy(memories.id)
      .all();
  }

  get(id: number): Memory | null {
    return this.db.select().from(memories).where(eq(memories.id, id)).get() ?? null;
  }

  save(input: { id?: number; topic?: string; detail?: string; kind?: MemoryKind; archived?: boolean }): Memory {
    if (input.id === undefined) {
      if (!input.topic?.trim() || !input.detail?.trim()) throw new Error("topic and detail are required");
      return this.db.insert(memories)
        .values({ topic: input.topic.trim(), detail: input.detail, kind: input.kind ?? "temporary", archived: input.archived === true })
        .returning().get();
    }
    const current = this.get(input.id);
    if (!current) throw new Error(`memory ${input.id} not found`);
    return this.db.update(memories)
      .set({
        topic: input.topic?.trim() || current.topic,
        detail: input.detail ?? current.detail,
        kind: input.kind ?? current.kind,
        archived: input.archived ?? current.archived,
      })
      .where(eq(memories.id, input.id))
      .returning().get();
  }

  delete(id: number): boolean {
    // bun:sqlite reports no row count, so ask for the deleted row back instead.
    return this.db.delete(memories).where(eq(memories.id, id)).returning({ id: memories.id }).get() !== undefined;
  }
}
