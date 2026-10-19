/** A books.db journal bridges its commit and jobs.db's idempotent wake-up. */
import { randomUUID } from "node:crypto";
import { and, eq, isNull } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
import type { BusDb } from "../drizzle/database.js";
import { transaction } from "../sqlite.js";
import { messages } from "./messageBook.js";
import type { Bus } from "../bus.js";

export const deliveries = sqliteTable("books_deliveries", {
  delivery_key: text("delivery_key").primaryKey(),
  message_id: integer("message_id").notNull().unique().references(() => messages.id, { onDelete: "cascade" }),
  publisher: text("publisher").notNull(),
  metadata: text("metadata"),
  enqueue: integer("enqueue", { mode: "boolean" }).notNull().default(true),
  job_id: integer("job_id"),
  attempts: integer("attempts").notNull().default(0),
  next_attempt_at: integer("next_attempt_at").notNull().default(0),
  last_error: text("last_error"),
});
export type Delivery = typeof deliveries.$inferSelect;

export class DeliveryBook {
  constructor(private readonly db: BusDb) {}

  get(messageId: number): Delivery | undefined {
    return this.db.select().from(deliveries).where(eq(deliveries.message_id, messageId)).get();
  }

  persist(input: { chat_id: number; contact_id: number; content: string; publisher: string; key?: string; metadata?: Record<string, unknown>; enqueue?: boolean }): { delivery: Delivery; duplicate: boolean } {
    const key = input.key ?? `local:${randomUUID()}`;
    return transaction(this.db.$client, () => {
      const prior = this.db.select().from(deliveries).where(eq(deliveries.delivery_key, key)).get();
      if (prior) {
        const message = this.db.select().from(messages).where(eq(messages.id, prior.message_id)).get();
        if (!message || message.chat_id !== input.chat_id || message.contact_id !== input.contact_id || message.content !== input.content || prior.metadata !== (input.metadata ? JSON.stringify(input.metadata) : null)) {
          throw new Error("message ID reused with different payload");
        }
        return { delivery: prior, duplicate: true };
      }
      const message = this.db.insert(messages).values({ chat_id: input.chat_id, contact_id: input.contact_id, content: input.content, created_at: new Date().toISOString() }).returning().get();
      const delivery = this.db.insert(deliveries).values({ delivery_key: key, message_id: message.id, publisher: input.publisher,
        metadata: input.metadata ? JSON.stringify(input.metadata) : null, enqueue: input.enqueue ?? true }).returning().get();
      return { delivery, duplicate: false };
    }, "immediate");
  }

  enqueue(bus: Bus, delivery: Delivery): number | null {
    if (!delivery.enqueue) return null;
    if (delivery.job_id !== null) return delivery.job_id;
    const metadata = delivery.metadata ? JSON.parse(delivery.metadata) as Record<string, unknown> : undefined;
    const id = bus.board("MessageDeliveryJob").publishOnce(delivery.delivery_key, { message_id: delivery.message_id, ...(metadata ? { metadata } : {}) }, delivery.publisher);
    this.db.update(deliveries).set({ job_id: id }).where(eq(deliveries.delivery_key, delivery.delivery_key)).run();
    return id;
  }

  reconcile(bus: Bus): void {
    for (const delivery of this.db.select().from(deliveries).where(and(eq(deliveries.enqueue, true), isNull(deliveries.job_id))).all()) this.enqueue(bus, delivery);
  }

  /** Attempt count and exponential cooldown survive process restarts. */
  retry(messageId: number, error: string, now = Date.now()): { attempts: number; nextAttemptAt: number; exhausted: boolean } {
    const delivery = this.get(messageId);
    if (!delivery) throw new Error("delivery journal missing");
    const attempts = delivery.attempts + 1;
    const nextAttemptAt = now + Math.min(60_000, 1000 * 2 ** Math.min(attempts - 1, 6));
    this.db.update(deliveries).set({ attempts, next_attempt_at: nextAttemptAt, last_error: error.slice(0, 2000) }).where(eq(deliveries.message_id, messageId)).run();
    return { attempts, nextAttemptAt, exhausted: attempts >= 8 };
  }
}
