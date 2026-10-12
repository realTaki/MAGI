/**
 * One-shot repair for databases from before the operator handle became `@user.magi`.
 *
 * This is not a migration framework — the schema belongs to `db/tables/` and the
 * migrations to `drizzle/`. It exists because a database that predates the rename still names
 * the operator `user` in rows and in event payloads, and a relay that reads those
 * rows would treat the operator as a stranger. It runs only when such a row is
 * still there, so it is safe to call on every open.
 */

import { eq } from "drizzle-orm";

import type { AspDb } from "./database.ts";
import { aspContactAllowlist, aspContacts } from "./tables/contacts.ts";
import { aspChatKeys } from "./tables/chatKeys.ts";
import { aspEventAcks } from "./tables/eventAcks.ts";
import { aspEvents } from "./tables/events.ts";
import { aspMessageKeys } from "./tables/messageKeys.ts";
import { aspMessageRecipients } from "./tables/messageRecipients.ts";
import { aspParticipants } from "./tables/participants.ts";
import { relayAllowlist, relayPrincipals } from "./tables/relayPrincipals.ts";
import { relayParticipants } from "./tables/relayParticipants.ts";

/** What the operator was called before this one: `user`, then `@user`. */
const LEGACY_HANDLES = ["user", "@user"];
const OPERATOR_HANDLE = "@user.magi";

/** Payload keys that carried the sender's handle. */
const HANDLE_KEYS = ["sender", "by", "agent", "invitee", "ended_by", "reopened_by"];

export function repairLegacyOperatorHandle(db: AspDb): void {
  for (const legacy of LEGACY_HANDLES) repairOperatorHandle(db, legacy);
}

function repairOperatorHandle(db: AspDb, legacy: string): void {
  const staleContact = db.select({ handle: aspContacts.handle }).from(aspContacts)
    .where(eq(aspContacts.handle, legacy)).get();
  const stalePrincipal = db.select({ handle: relayPrincipals.handle }).from(relayPrincipals)
    .where(eq(relayPrincipals.handle, legacy)).get();
  if (staleContact === undefined && stalePrincipal === undefined) {
    return;
  }
  db.transaction((tx) => {
    tx.update(aspContacts).set({ handle: OPERATOR_HANDLE })
      .where(eq(aspContacts.handle, legacy))
      .run();
    tx.update(aspContactAllowlist).set({ allowed_handle: OPERATOR_HANDLE })
      .where(eq(aspContactAllowlist.allowed_handle, legacy)).run();
    tx.update(aspParticipants).set({ handle: OPERATOR_HANDLE })
      .where(eq(aspParticipants.handle, legacy)).run();
    tx.update(relayPrincipals).set({ handle: OPERATOR_HANDLE })
      .where(eq(relayPrincipals.handle, legacy))
      .run();
    tx.update(relayAllowlist).set({ allowed_handle: OPERATOR_HANDLE })
      .where(eq(relayAllowlist.allowed_handle, legacy)).run();
    tx.update(relayParticipants).set({ handle: OPERATOR_HANDLE })
      .where(eq(relayParticipants.handle, legacy)).run();
    tx.update(aspMessageRecipients).set({ handle: OPERATOR_HANDLE })
      .where(eq(aspMessageRecipients.handle, legacy)).run();
    tx.update(aspEventAcks).set({ handle: OPERATOR_HANDLE })
      .where(eq(aspEventAcks.handle, legacy)).run();
    tx.update(aspMessageKeys).set({ sender: OPERATOR_HANDLE })
      .where(eq(aspMessageKeys.sender, legacy)).run();
    tx.update(aspChatKeys).set({ creator: OPERATOR_HANDLE })
      .where(eq(aspChatKeys.creator, legacy)).run();
    // Payloads mention who spoke, and the rename happened while events were stored.
    for (const event of tx.select({ id: aspEvents.event_id, payload: aspEvents.payload_json }).from(aspEvents).all()) {
      const payload = { ...event.payload };
      let changed = false;
      for (const key of HANDLE_KEYS) {
        if (payload[key] === legacy) {
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
