import { randomUUID } from "node:crypto";
import { and, eq, sql } from "drizzle-orm";
import { integer, sqliteTable, text, unique } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";

export const chats = sqliteTable("books_chats", {
  id: integer("id").primaryKey(),
  peer_id: text("peer_id"),
  conversation_uuid: text("conversation_uuid"),
  channel: text("channel").notNull(),
  delivery_address: text("delivery_address").notNull(),
  instruction: text("instruction").notNull().default(""),
  topic: text("topic").notNull().default(""),
  info: text("info").notNull().default(""),
  summary: text("summary").notNull().default(""),
  /** How far this chat's channel stream has been read; see `read`. */
  last_sequence: integer("last_sequence").notNull().default(-1),
}, (table) => [unique("books_chats_channel_address").on(table.channel, table.delivery_address)]);

export type Chat = typeof chats.$inferSelect;

export class ChatBook {
  constructor(private readonly db: BusDb) {}

  get(id: number): Chat | null {
    return this.db.select().from(chats).where(eq(chats.id, id)).get() ?? null;
  }

  list(channel?: string): Chat[] {
    if (channel === undefined) return this.db.select().from(chats).orderBy(chats.id).all();
    return this.db.select().from(chats).where(eq(chats.channel, channel)).orderBy(chats.id).all();
  }

  forChannel(channel: string, address: string): Chat {
    this.db.insert(chats).values({ channel, delivery_address: address }).onConflictDoNothing().run();
    return this.db.select().from(chats)
      .where(and(eq(chats.channel, channel), eq(chats.delivery_address, address)))
      .get()!;
  }

  forPeer(principal: string, conversationUUID: string = randomUUID()): Chat {
    if (!/^[a-f0-9]{64}$/.test(principal)) throw new Error("invalid peer principal");
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(conversationUUID)) throw new Error("invalid conversation UUID");
    const address = `${principal}/${conversationUUID}`;
    this.db.insert(chats).values({ channel: "peer", delivery_address: address, peer_id: principal, conversation_uuid: conversationUUID }).onConflictDoNothing().run();
    return this.forChannel("peer", address);
  }

  updateSummary(id: number, summary: string): void {
    this.db.update(chats).set({ summary }).where(eq(chats.id, id)).run();
  }

  setTopic(id: number, topic: string): void {
    this.db.update(chats).set({ topic }).where(eq(chats.id, id)).run();
  }

  setInfo(id: number, info: string): void {
    this.db.update(chats).set({ info }).where(eq(chats.id, id)).run();
  }

  /**
   * How far this workspace has read a channel's stream: -1 until anything is taken in.
   *
   * Channels that number their events can replay what
   * they have not seen acknowledged. The reader records the last number it took in, so a
   * replay of something already handled is recognised instead of handled twice. The
   * reading lives on the chat because a chat *is* a channel address.
   */
  read(channel: string, address: string): number {
    return this.db.select({ last_sequence: chats.last_sequence }).from(chats)
      .where(and(eq(chats.channel, channel), eq(chats.delivery_address, address)))
      .get()?.last_sequence ?? -1;
  }

  /** Reading is what advances it, and it never goes backwards. It creates the chat if new. */
  markRead(channel: string, address: string, sequence: number): void {
    this.db.insert(chats).values({ channel, delivery_address: address, last_sequence: sequence })
      .onConflictDoUpdate({
        target: [chats.channel, chats.delivery_address],
        set: { last_sequence: sql`max(${chats.last_sequence}, excluded.last_sequence)` },
      })
      .run();
  }
}
