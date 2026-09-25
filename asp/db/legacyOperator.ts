/**
 * One-shot repair for databases from before the operator handle became `@user`.
 *
 * This is not a migration framework — the schema belongs to `schema.ts` and
 * `drizzle/`. It exists because a database that predates the rename still names
 * the operator `user` in rows and in event payloads, and a relay that reads those
 * rows would treat the operator as a stranger. It runs only when such a row is
 * still there, so it is safe to call on every open.
 */

import { eq } from "drizzle-orm";

import type { AspDb } from "./database.ts";
import {
  aspAgents,
  aspChatKeys,
  aspEventAcks,
  aspEvents,
  aspMessageKeys,
  aspMessageRecipients,
  aspParticipants,
} from "./schema.ts";

const LEGACY_HANDLE = "user";
const OPERATOR_HANDLE = "@user";

/** Payload keys that carried the sender's handle. */
const HANDLE_KEYS = ["sender", "by", "agent", "invitee", "ended_by", "reopened_by"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function repairLegacyOperatorHandle(db: AspDb): void {
  const stale = db.select({ handle: aspAgents.handle }).from(aspAgents)
    .where(eq(aspAgents.handle, LEGACY_HANDLE)).get();
  if (stale === undefined) {
    return;
  }
  db.transaction((tx) => {
    const record = tx.select({ record: aspAgents.record_json }).from(aspAgents)
      .where(eq(aspAgents.handle, LEGACY_HANDLE)).get()?.record;
    tx.update(aspAgents)
      .set({
        handle: OPERATOR_HANDLE,
        record_json: isRecord(record) ? { ...record, handle: OPERATOR_HANDLE } : record,
      })
      .where(eq(aspAgents.handle, LEGACY_HANDLE))
      .run();
    tx.update(aspParticipants).set({ handle: OPERATOR_HANDLE })
      .where(eq(aspParticipants.handle, LEGACY_HANDLE)).run();
    tx.update(aspMessageRecipients).set({ handle: OPERATOR_HANDLE })
      .where(eq(aspMessageRecipients.handle, LEGACY_HANDLE)).run();
    tx.update(aspEventAcks).set({ handle: OPERATOR_HANDLE })
      .where(eq(aspEventAcks.handle, LEGACY_HANDLE)).run();
    tx.update(aspMessageKeys).set({ sender: OPERATOR_HANDLE })
      .where(eq(aspMessageKeys.sender, LEGACY_HANDLE)).run();
    tx.update(aspChatKeys).set({ creator: OPERATOR_HANDLE })
      .where(eq(aspChatKeys.creator, LEGACY_HANDLE)).run();
    // Payloads mention who spoke, and the rename happened while events were stored.
    for (const event of tx.select({ id: aspEvents.event_id, payload: aspEvents.payload_json }).from(aspEvents).all()) {
      const payload = { ...event.payload };
      let changed = false;
      for (const key of HANDLE_KEYS) {
        if (payload[key] === LEGACY_HANDLE) {
          payload[key] = OPERATOR_HANDLE;
          changed = true;
        }
      }
      if (changed) {
        tx.update(aspEvents).set({ payload_json: payload }).where(eq(aspEvents.event_id, event.id)).run();
      }
    }
  }, { behavior: "immediate" });
}
