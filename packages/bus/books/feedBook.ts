import { createHash } from "node:crypto";
import { and, desc, eq, sql } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";

export const feedEvidence = sqliteTable("books_feed_evidence", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  tool: text("tool").notNull(), job_id: integer("job_id").notNull().unique(),
  source_fingerprint: text("source_fingerprint").notNull(), config_revision: integer("config_revision").notNull(),
  kind: text("kind").notNull().default("search-snippet"), content: text("content").notNull(),
  urls: text("urls", { mode: "json" }).$type<string[]>().notNull(),
  created_at: text("created_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
});
export type FeedSource = { evidence_id: number; url: string };
export const feedItems = sqliteTable("books_feed_items", {
  id: integer("id").primaryKey({ autoIncrement: true }), dedupe_key: text("dedupe_key").notNull().unique(),
  version: integer("version").notNull().default(1), title: text("title").notNull(), body: text("body").notNull(),
  why_relevant: text("why_relevant").notNull(), sources: text("sources", { mode: "json" }).$type<FeedSource[]>().notNull(),
  feedback: text("feedback").notNull().default(""),
  created_at: text("created_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
  updated_at: text("updated_at").notNull().default(sql`(CURRENT_TIMESTAMP)`),
});
export type FeedItem = typeof feedItems.$inferSelect;
export type FeedEvidence = typeof feedEvidence.$inferSelect;

/** Public HTTP(S) citations only. This is URL hygiene, not a claim we fetched a page. */
export function feedUrl(raw: unknown): string {
  if (typeof raw !== "string" || raw.length > 4096) throw new Error("invalid source URL");
  const url = new URL(raw);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("source must be an HTTP(S) URL without credentials");
  url.hash = "";
  return url.href;
}
function bounded(value: unknown, name: string, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > max) throw new Error(`${name} must contain 1..${max} characters`);
  return value.trim();
}
export class FeedBook {
  constructor(private readonly db: BusDb) {}
  evidence(id: number): FeedEvidence | null { return this.db.select().from(feedEvidence).where(eq(feedEvidence.id, id)).get() ?? null; }
  recordEvidence(tool: string, job_id: number, content: string, provenance: { source_fingerprint: string; config_revision: number }): FeedEvidence {
    // Keep bounded text actually returned by the tool, not model-supplied citations.
    const excerpt = content.slice(0, 30_000);
    const urls = [...new Set((excerpt.match(/https?:\/\/[^\s<>"'`\\]+/g) ?? []).flatMap(raw => {
      try { return [feedUrl(raw.replace(/[),.;\]}]+$/, ""))]; } catch { return []; }
    }))].slice(0, 100);
    return this.db.insert(feedEvidence).values({ tool, job_id, content: excerpt, urls, ...provenance }).returning().get();
  }
  list(includeDismissed = false): FeedItem[] {
    return this.db.select().from(feedItems).where(includeDismissed ? undefined : sql`${feedItems.feedback} != 'dismiss'`).orderBy(desc(feedItems.id)).limit(100).all();
  }
  get(id: number): FeedItem | null { return this.db.select().from(feedItems).where(eq(feedItems.id, id)).get() ?? null; }
  save(input: { title: string; body: string; why_relevant: string; sources: FeedSource[] }): FeedItem {
    const title = bounded(input.title, "title", 160), body = bounded(input.body, "body", 1800), why = bounded(input.why_relevant, "why_relevant", 500);
    if (!Array.isArray(input.sources) || input.sources.length < 1 || input.sources.length > 8) throw new Error("one to eight evidence-backed sources are required");
    const sources = input.sources.map(source => {
      const evidence = this.evidence(source.evidence_id), url = feedUrl(source.url);
      if (!evidence?.urls.includes(url)) throw new Error("citation was not observed in the recorded research result");
      return { evidence_id: evidence.id, url };
    });
    // Source-set identity, deliberately independent of generated title. A dismissed
    // source set is never revived by another generation or a process restart.
    const dedupe_key = createHash("sha256").update([...new Set(sources.map(s => s.url))].sort().join("\n")).digest("hex");
    const inserted = this.db.insert(feedItems).values({ dedupe_key, title, body, why_relevant: why, sources }).onConflictDoNothing().returning().get();
    return inserted ?? this.db.select().from(feedItems).where(eq(feedItems.dedupe_key, dedupe_key)).get()!;
  }
  feedback(id: number, value: string, expectedVersion: number): FeedItem {
    if (!["", "useful", "dismiss"].includes(value)) throw new Error("invalid feedback");
    if (!Number.isSafeInteger(expectedVersion) || expectedVersion < 1) throw new Error("expected_version is required");
    const item = this.db.update(feedItems).set({ feedback: value, version: sql`${feedItems.version} + 1`, updated_at: sql`(CURRENT_TIMESTAMP)` })
      .where(and(eq(feedItems.id, id), eq(feedItems.version, expectedVersion))).returning().get();
    if (!item) throw new Error("feed item changed or no longer exists; reload before retrying");
    return item;
  }
}
