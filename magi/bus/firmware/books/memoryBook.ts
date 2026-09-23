import type { Database } from "bun:sqlite";

export type MemoryKind = "temporary" | "short_term" | "long_term";
export type Memory = { id: number; topic: string; detail: string; kind: MemoryKind; archived: number; created_at: string };

export class MemoryBook {
  constructor(private readonly db: Database) {}

  list(includeArchived = false): Memory[] {
    return this.db.prepare(`SELECT * FROM books_memories ${includeArchived ? "" : "WHERE archived = 0"} ORDER BY id`).all() as Memory[];
  }

  get(id: number): Memory | null {
    return (this.db.prepare("SELECT * FROM books_memories WHERE id = ?").get(id) as Memory | undefined) ?? null;
  }

  save(input: { id?: number; topic?: string; detail?: string; kind?: MemoryKind; archived?: boolean }): Memory {
    if (input.id === undefined) {
      if (!input.topic?.trim() || !input.detail?.trim()) throw new Error("topic and detail are required");
      const result = this.db.prepare("INSERT INTO books_memories (topic, detail, kind, archived) VALUES (?, ?, ?, ?)")
        .run(input.topic.trim(), input.detail, input.kind ?? "temporary", input.archived ? 1 : 0);
      return this.get(Number(result.lastInsertRowid))!;
    }
    const current = this.get(input.id);
    if (!current) throw new Error(`memory ${input.id} not found`);
    this.db.prepare("UPDATE books_memories SET topic = ?, detail = ?, kind = ?, archived = ? WHERE id = ?")
      .run(input.topic?.trim() || current.topic, input.detail ?? current.detail, input.kind ?? current.kind, input.archived === undefined ? current.archived : input.archived ? 1 : 0, input.id);
    return this.get(input.id)!;
  }

  delete(id: number): boolean {
    return this.db.prepare("DELETE FROM books_memories WHERE id = ?").run(id).changes === 1;
  }
}
