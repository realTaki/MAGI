import { and, eq, sql } from "drizzle-orm";
import { integer, sqliteTable, text, unique } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";

/**
 * How far this workspace has read a channel's stream.
 *
 * Channels that number their events — ASP numbers every session event — can replay what
 * they have not seen acknowledged. The reader records the last number it took in, so a
 * replay of something already handled is recognised instead of being handled twice.
 */
export const channelCursors = sqliteTable("books_channel_cursors", {
  id: integer("id").primaryKey(),
  channel: text("channel").notNull(),
  address: text("address").notNull(),
  last_sequence: integer("last_sequence").notNull().default(0),
}, (table) => [unique("books_channel_cursors_target").on(table.channel, table.address)]);

export class ChannelCursorBook {
  constructor(private readonly db: BusDb) {}

  /** -1 until anything is taken in: ASP numbers a session's events from 0. */
  read(channel: string, address: string): number {
    return this.db.select({ last_sequence: channelCursors.last_sequence }).from(channelCursors)
      .where(and(eq(channelCursors.channel, channel), eq(channelCursors.address, address)))
      .get()?.last_sequence ?? -1;
  }

  /** Reading is what advances it, and it never goes backwards. */
  markRead(channel: string, address: string, sequence: number): void {
    this.db.insert(channelCursors).values({ channel, address, last_sequence: sequence })
      .onConflictDoUpdate({
        target: [channelCursors.channel, channelCursors.address],
        set: { last_sequence: sql`max(${channelCursors.last_sequence}, excluded.last_sequence)` },
      })
      .run();
  }
}
