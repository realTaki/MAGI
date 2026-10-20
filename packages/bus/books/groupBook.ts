import { prepareRich, applyReaction, validateReaction, type Reaction } from "../richMessage.js";
import { validateAttachmentInputs, type AttachmentInput } from "../attachments.js";
/** Immutable group history on a trusted private network. Replication unions events, never overwrites messages.
 * Membership is a creator-sequenced routing log, not authentication or consensus.
 */
import { createHash, createPublicKey, randomUUID } from "node:crypto";
import { validateChatInput } from "../chat.js";
import type { Bus } from "../bus.js";
import type { BusDb } from "../drizzle/database.js";
import { transaction } from "../sqlite.js";
import { MAGI_CONTACT_ID, validatePeerEndpoint, validatePeerPrincipal } from "./contactBook.js";

export type GroupMember = { principal: string; endpoint: string };
export type GroupMembership = { v: 1; kind: "membership"; conversationUUID: string; owner: string; epoch: number; previousDigest: string | null; title: string; members: GroupMember[] };
export type GroupEvent = { v: 1; kind: "message"; conversationUUID: string; eventId: string; author: string; sequence: number; lamport: number; epoch: number; content: string; replyTo?: string; requestReply?: boolean; attachments?: AttachmentInput[]; reaction?: Reaction };
export type GroupMembershipEnvelope = { publicKey: string; payload: GroupMembership };
export type GroupEventEnvelope = { publicKey: string; payload: GroupEvent };
export type Group = { conversation_uuid: string; owner: string; title: string; epoch: number; chat_id: number };
export type GroupReceipt = { event: GroupEventEnvelope; message_id: number; job_id: number | null; duplicate: boolean };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function groupPrincipal(publicKey: string): string {
  const key = createPublicKey(publicKey);
  if (key.asymmetricKeyType !== "rsa" || (key.asymmetricKeyDetails?.modulusLength ?? 0) < 2048) throw new Error("invalid group identity key");
  return createHash("sha256").update(key.export({ type: "spki", format: "der" })).digest("hex");
}
function digest(value: unknown): string { return createHash("sha256").update(JSON.stringify(value)).digest("hex"); }
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }
function checkEnvelope(value: unknown): string {
  if (!object(value) || typeof value.publicKey !== "string" || value.publicKey.length > 8192 || !object(value.payload) || JSON.stringify(value.payload).length > 15 * 1024 * 1024) throw new Error("invalid group envelope");
  // The public key supplies a stable label, not proof of the remote process.
  return groupPrincipal(value.publicKey);
}
function checkMembers(value: unknown): asserts value is GroupMember[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 64) throw new Error("groups require 1 to 64 explicit members");
  const seen = new Set<string>();
  for (const member of value) {
    if (!object(member) || typeof member.principal !== "string" || typeof member.endpoint !== "string") throw new Error("invalid member");
    validatePeerPrincipal(member.principal); validatePeerEndpoint(member.endpoint);
    if (seen.has(member.principal)) throw new Error("duplicate member"); seen.add(member.principal);
  }
}

export class GroupBook {
  constructor(private readonly db: BusDb, private readonly bus: Bus) {}
  get(uuid: string): Group | null {
    return (this.db.$client.prepare("SELECT * FROM books_groups WHERE conversation_uuid = ?").get(uuid) as Group | undefined) ?? null;
  }
  list(): Group[] { return this.db.$client.prepare("SELECT * FROM books_groups ORDER BY conversation_uuid").all() as Group[]; }
  membership(uuid: string, epoch?: number): GroupMembershipEnvelope {
    const group = this.get(uuid); if (!group) throw new Error("group not found");
    const row = this.db.$client.prepare("SELECT envelope FROM books_group_memberships WHERE conversation_uuid = ? AND epoch = ?").get(uuid, epoch ?? group.epoch);
    if (!row) throw new Error("group membership epoch missing");
    return JSON.parse(String(row.envelope)) as GroupMembershipEnvelope;
  }
  memberships(uuid: string, afterEpoch = 0, limit = 100): GroupMembershipEnvelope[] {
    if (!Number.isSafeInteger(afterEpoch) || afterEpoch < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new Error("invalid membership page");
    return this.db.$client.prepare("SELECT envelope FROM books_group_memberships WHERE conversation_uuid = ? AND epoch > ? ORDER BY epoch LIMIT ?").all(uuid, afterEpoch, limit).map(row => JSON.parse(String(row.envelope)) as GroupMembershipEnvelope);
  }
  create(input: { title: string; members: GroupMember[]; endpoint?: string }): Group {
    const owner = groupPrincipal(this.bus.identity.publicKey);
    const endpoint = validatePeerEndpoint(input.endpoint ?? this.bus.settings.get("peer.url") ?? this.bus.settings.get("peer.reachableURL") ?? "");
    const members = [{ principal: owner, endpoint }, ...input.members.filter(member => member.principal !== owner)];
    checkMembers(members);
    const snapshot = this.envelope({ v: 1, kind: "membership", conversationUUID: randomUUID(), owner, epoch: 1, previousDigest: null, title: input.title, members });
    return this.offerMembership(snapshot);
  }
  updateMembers(uuid: string, members: GroupMember[]): GroupMembershipEnvelope {
    const group = this.get(uuid); const owner = groupPrincipal(this.bus.identity.publicKey);
    if (!group || group.owner !== owner) throw new Error("only group creator may change membership");
    checkMembers(members);
    if (!members.some(member => member.principal === owner)) throw new Error("group creator must remain a member");
    const snapshot = this.envelope({ ...this.membership(uuid).payload, epoch: group.epoch + 1, previousDigest: digest(this.membership(uuid)), members });
    this.offerMembership(snapshot); return snapshot;
  }
  /** Import membership directly; all participating processes share the trusted LAN. */
  offerMembership(envelope: GroupMembershipEnvelope): Group {
    const signer = checkEnvelope(envelope); const value = envelope.payload;
    if (value.v !== 1 || value.kind !== "membership" || !UUID.test(value.conversationUUID) || value.owner !== signer || !Number.isSafeInteger(value.epoch) || value.epoch < 1 || typeof value.title !== "string" || value.title.length > 200) throw new Error("invalid group membership");
    checkMembers(value.members);
    if (!value.members.some(member => member.principal === value.owner)) throw new Error("membership omits creator");
    return transaction(this.db.$client, () => {
      const current = this.get(value.conversationUUID);
      if (current) {
        if (current.owner !== signer) throw new Error("group owner identity mismatch");
        if (value.epoch <= current.epoch) {
          if (digest(this.membership(value.conversationUUID, value.epoch)) !== digest(envelope)) throw new Error("conflicting membership epoch");
          return current;
        }
        if (value.epoch !== current.epoch + 1 || value.previousDigest !== digest(this.membership(value.conversationUUID))) throw new Error("membership chain has a gap or conflict");
      } else {
        if (value.epoch !== 1 || value.previousDigest !== null) throw new Error("initial membership must be epoch one");
        const self = groupPrincipal(this.bus.identity.publicKey);
        if (!value.members.some(member => member.principal === self)) throw new Error("not invited to group");
        const chat = this.bus.chats.forChannel("group", value.conversationUUID);
        this.db.$client.prepare("INSERT INTO books_groups(conversation_uuid, owner, title, epoch, chat_id) VALUES(?, ?, ?, ?, ?)").run(value.conversationUUID, signer, value.title, value.epoch, chat.id);
      }
      this.db.$client.prepare("INSERT INTO books_group_memberships(conversation_uuid, epoch, envelope) VALUES(?, ?, ?)").run(value.conversationUUID, value.epoch, JSON.stringify(envelope));
      this.db.$client.prepare("UPDATE books_groups SET title = ?, epoch = ? WHERE conversation_uuid = ?").run(value.title, value.epoch, value.conversationUUID);
      return this.get(value.conversationUUID)!;
    }, "immediate");
  }
  /** Membership selects replication recipients; it is not an authorization check. */
  allows(uuid: string, principal: string): boolean {
    const group = this.get(uuid); if (!group) return false;
    const members = this.membership(uuid).payload.members;
    return members.some(member => member.principal === principal) && members.some(member => member.principal === groupPrincipal(this.bus.identity.publicKey));
  }
  append(uuid: string, content: string, replyTo?: string, systemErrorSource?: number, rich?: { attachments?: AttachmentInput[]; reaction?: Reaction; requestReply?: boolean }): GroupReceipt {
    const author = groupPrincipal(this.bus.identity.publicKey);
    if (!this.allows(uuid, author)) throw new Error("local process is not a current group member");
    {
      const sequence = Number(this.db.$client.prepare("SELECT COALESCE(MAX(sequence), 0) + 1 AS n FROM books_group_events WHERE conversation_uuid = ? AND author = ?").get(uuid, author)!.n);
      const lamport = Number(this.db.$client.prepare("SELECT COALESCE(MAX(lamport), 0) + 1 AS n FROM books_group_events WHERE conversation_uuid = ?").get(uuid)!.n);
      const event = this.envelope({ v: 1, kind: "message", conversationUUID: uuid, eventId: randomUUID(), author, sequence, lamport, epoch: this.get(uuid)!.epoch, content, ...(replyTo ? { replyTo } : {}), ...(rich?.attachments?.length ? { attachments: rich.attachments } : {}), ...(rich?.reaction ? { reaction: rich.reaction } : {}), ...(rich?.requestReply ? { requestReply: true } : {}) });
      return this.ingest(event, systemErrorSource);
    }
  }
  ingest(event: GroupEventEnvelope, systemErrorSource?: number): GroupReceipt {
    const signer = checkEnvelope(event); const value = event.payload;
    if (value.v !== 1 || value.kind !== "message" || value.author !== signer || !UUID.test(value.conversationUUID) || !UUID.test(value.eventId) || !Number.isSafeInteger(value.sequence) || value.sequence < 1 || !Number.isSafeInteger(value.lamport) || value.lamport < 1 || !Number.isSafeInteger(value.epoch) || value.epoch < 1 || typeof value.content !== "string" || value.content.length > 32000 || (value.replyTo !== undefined && (typeof value.replyTo !== "string" || !UUID.test(value.replyTo)))) throw new Error("invalid group event");
    if (value.attachments !== undefined) validateAttachmentInputs(value.attachments);
    if (value.requestReply !== undefined && typeof value.requestReply !== "boolean") throw new Error("invalid reply request");
    if (value.reaction) validateReaction(value.reaction);
    if (value.reaction && (value.content || value.attachments?.length || value.replyTo || value.requestReply)) throw new Error("reaction cannot carry chat content");
    const group = this.get(value.conversationUUID);
    const self = groupPrincipal(this.bus.identity.publicKey);
    if (!group || !this.allows(value.conversationUUID, self)) throw new Error("local process is not a current group member");
    const member = this.membership(value.conversationUUID, value.epoch).payload.members.find(member => member.principal === signer);
    if (!member) throw new Error("event author was not a member in membership epoch");
    const receipt = transaction(this.db.$client, () => {
      const prior = this.db.$client.prepare("SELECT envelope, message_id FROM books_group_events WHERE conversation_uuid = ? AND author = ? AND (event_id = ? OR sequence = ?)").get(value.conversationUUID, signer, value.eventId, value.sequence);
      if (prior) {
        if (digest(JSON.parse(String(prior.envelope))) !== digest(event)) throw new Error("group author equivocation: event ID or sequence reused");
        return { event, duplicate: true, message_id: Number(prior.message_id) };
      }
      const local = signer === self;
      // Historical membership endpoints are not routing updates. A peer can move
      // after composing an event; use its pinned identity without reverting its URL.
      const known = this.bus.contacts.list().find(contact => contact.peer_id === signer);
      const contact = local ? this.bus.contacts.get(MAGI_CONTACT_ID)! : known ?? this.bus.contacts.forPeer({ principal: signer, endpoint: member.endpoint });
      if (!local && contact.peer_principal !== signer) throw new Error("event author identity mismatch");
      validateChatInput(this.bus, group.chat_id, contact.id, { content: value.content }, local);
      const prepared = prepareRich(this.bus, group.chat_id, value.content, { attachments: value.attachments });
      if (value.replyTo) {
        prepared.rich.reply_to = value.replyTo;
        prepared.rich.reply_excerpt = this.bus.messages.findPeer(group.chat_id, value.replyTo)?.content.slice(0, 2000);
      }
      if (value.reaction) prepared.rich.reaction = value.reaction;
      const metadata = { rich: prepared.rich, ...(local && systemErrorSource ? { system_error_source: systemErrorSource } : {}), group: { event }, peer: { messageId: value.eventId, conversationUUID: value.conversationUUID, ...(value.replyTo ? { replyTo: value.replyTo } : {}) } };
      const stored = this.bus.deliveries.persist({ chat_id: group.chat_id, contact_id: contact.id, content: prepared.content, publisher: local ? "agent" : "peer", key: `group:${value.conversationUUID}:${signer}:${value.eventId}`, metadata, enqueue: local || ((!value.replyTo || value.requestReply === true) && !value.reaction) });
      this.db.$client.prepare("INSERT INTO books_group_events(conversation_uuid, author, event_id, sequence, lamport, epoch, envelope, message_id) VALUES(?, ?, ?, ?, ?, ?, ?, ?)").run(value.conversationUUID, signer, value.eventId, value.sequence, value.lamport, value.epoch, JSON.stringify(event), stored.delivery.message_id);
      if (value.reaction) applyReaction(this.bus, group.chat_id, signer, value.reaction);
      this.bus.chatMembers.add(group.chat_id, contact.id);
      return { event, duplicate: false, message_id: stored.delivery.message_id };
    }, "immediate");
    const delivery = this.bus.deliveries.get(receipt.message_id)!;
    return { ...receipt, job_id: this.bus.deliveries.enqueue(this.bus, delivery) };
  }
  /** Storage-order cursor is transport-local; restart each anti-entropy round at zero.
   * No per-sender high-water shortcut: gaps and reordered events remain discoverable.
   */
  events(uuid: string, options: { after?: number; limit?: number } = {}): { events: GroupEventEnvelope[]; nextCursor: number | null } {
    const after = options.after ?? 0, limit = options.limit ?? 100;
    if (!Number.isSafeInteger(after) || after < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new Error("invalid group event page");
    const rows = this.db.$client.prepare("SELECT id, envelope FROM books_group_events WHERE conversation_uuid = ? AND id > ? ORDER BY id LIMIT ?").all(uuid, after, limit + 1);
    const selected = rows.slice(0, limit);
    return { events: selected.map(row => JSON.parse(String(row.envelope)) as GroupEventEnvelope), nextCursor: rows.length > limit ? Number(selected.at(-1)!.id) : null };
  }
  /** Display is deterministic regardless of arrival order. */
  history(uuid: string, limit = 100): GroupEventEnvelope[] {
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 1000) throw new Error("invalid group history limit");
    return this.db.$client.prepare("SELECT envelope FROM (SELECT * FROM books_group_events WHERE conversation_uuid = ? ORDER BY lamport DESC, author DESC, event_id DESC LIMIT ?) ORDER BY lamport, author, event_id").all(uuid, limit).map(row => JSON.parse(String(row.envelope)) as GroupEventEnvelope);
  }
  private envelope<T extends GroupEvent | GroupMembership>(payload: T): { publicKey: string; payload: T } {
    return { publicKey: this.bus.identity.publicKey, payload };
  }
}
