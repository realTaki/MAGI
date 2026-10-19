import { MAGI_CONTACT_ID, type Bus } from "@magi/bus";
import type { SignedGroupEvent, SignedGroupMembership } from "@magi/bus/books/groupBook.js";
import { PeerClient, type GroupWire, type SyncRequest, type SyncResponse } from "./client.js";
import { PeerError, record } from "./protocol.js";

/** Full paginated anti-entropy: no sender high-water mark can hide reordered gaps. */
export class GroupReplica {
  private readonly pages = new Map<string, { after: number; sentEpoch: number; due: number }>();
  constructor(private readonly bus: Bus, private readonly client: PeerClient) {}
  private accepted(principal: string) {
    const contact = this.bus.contacts.list().find(contact => contact.peer_id === principal);
    if (contact?.peer_trust !== "accepted" || !contact.peer_url || contact.peer_principal !== principal) throw new PeerError("peer route is not accepted", 403);
    return contact;
  }
  receive(principal: string, wire: GroupWire): { duplicate: boolean } {
    this.accepted(principal);
    const event = wire.group.event;
    if (!record(event) || !record(event.payload) || typeof event.payload.conversationUUID !== "string") throw new PeerError("invalid group event");
    if (!this.bus.groups.get(event.payload.conversationUUID)) throw new PeerError("group invitation required", 409);
    if (!this.bus.groups.allows(event.payload.conversationUUID, principal)) throw new PeerError("group is pending or sender is not a current member", 403);
    return this.bus.groups.ingest(event);
  }
  respond(principal: string, input: unknown): SyncResponse {
    this.accepted(principal);
    if (!record(input) || Object.keys(input).some(key => !["conversationUUID", "after", "afterEpoch", "memberships"].includes(key)) || typeof input.conversationUUID !== "string" || (input.after !== undefined && (!Number.isSafeInteger(input.after) || Number(input.after) < 0)) || (input.afterEpoch !== undefined && (!Number.isSafeInteger(input.afterEpoch) || Number(input.afterEpoch) < 0)) || (input.memberships !== undefined && (!Array.isArray(input.memberships) || input.memberships.length > 8))) throw new PeerError("invalid sync request");
    const request = input as SyncRequest;
    // The original creator signature authorizes membership changes, not this relay.
    for (const membership of request.memberships ?? []) {
      if (membership?.payload?.conversationUUID !== request.conversationUUID) throw new PeerError("membership conversation mismatch");
      this.bus.groups.offerMembership(membership);
    }
    const group = this.bus.groups.get(request.conversationUUID);
    if (!group || !this.bus.groups.allows(request.conversationUUID, principal)) throw new PeerError("group is pending or peer is not a current member", 403);
    const memberships = this.bus.groups.memberships(request.conversationUUID, request.afterEpoch ?? 0, 8);
    const lastEpoch = memberships.at(-1)?.payload.epoch ?? request.afterEpoch ?? 0;
    const moreMemberships = lastEpoch < group.epoch;
    const page = moreMemberships ? { events: [], nextCursor: request.after ?? 0 } : this.bus.groups.events(request.conversationUUID, { after: request.after, limit: 8 });
    return { ...page, memberships, moreMemberships, receivedEpoch: group.epoch };
  }
  async deliver(event: SignedGroupEvent): Promise<void> {
    const uuid = event.payload.conversationUUID;
    if (!this.bus.groups.allows(uuid, this.client.principal)) throw new PeerError("local member is not authorized for group", 403);
    const peers = this.bus.groups.membership(uuid).payload.members.filter(member => member.principal !== this.client.principal);
    for (const peer of peers) {
      const contact = this.accepted(peer.principal);
      // Use the independently accepted contact route, never a third-party callback.
      await this.client.sendGroup(contact.peer_url!, peer.principal, { group: { event } });
    }
  }
  /** Public for deterministic loopback integration tests and explicit resync tools. */
  async syncPeer(uuid: string, principal: string): Promise<boolean> {
    const contact = this.accepted(principal);
    if (!this.bus.groups.allows(uuid, this.client.principal)) throw new PeerError("local group is not accepted", 403);
    const key = `${uuid}:${principal}`;
    const state = this.pages.get(key) ?? { after: 0, sentEpoch: 0, due: 0 };
    const memberships = this.bus.groups.memberships(uuid, state.sentEpoch, 8);
    const response = await this.client.sync(contact.peer_url!, principal, {
      conversationUUID: uuid, after: state.after, afterEpoch: this.bus.groups.get(uuid)!.epoch, memberships,
    });
    for (const membership of response.memberships) {
      if (membership?.payload?.conversationUUID !== uuid) throw new PeerError("membership conversation mismatch");
      this.bus.groups.offerMembership(membership as SignedGroupMembership);
    }
    if (!this.bus.groups.allows(uuid, principal)) throw new PeerError("remote member was removed", 403);
    for (const event of response.events) {
      if (event?.payload?.conversationUUID !== uuid) throw new PeerError("event conversation mismatch");
      this.bus.groups.ingest(event);
    }
    const more = response.moreMemberships || response.nextCursor !== null || response.receivedEpoch < this.bus.groups.get(uuid)!.epoch;
    this.pages.set(key, { after: response.nextCursor ?? 0, sentEpoch: response.receivedEpoch, due: more ? 0 : Date.now() + 10_000 });
    return more;
  }
  async poll(): Promise<boolean> {
    for (const group of this.bus.groups.list()) {
      if (!this.bus.groups.allows(group.conversation_uuid, this.client.principal)) continue;
      for (const member of this.bus.groups.membership(group.conversation_uuid).payload.members) {
        if (member.principal === this.client.principal) continue;
        const key = `${group.conversation_uuid}:${member.principal}`;
        if ((this.pages.get(key)?.due ?? 0) > Date.now()) continue;
        try { await this.syncPeer(group.conversation_uuid, member.principal); }
        catch {
          // Offline/pending peers retain durable history. The next round starts at
          // zero; a process restart is equally safe and never deletes an event.
          this.pages.set(key, { after: 0, sentEpoch: 0, due: Date.now() + 10_000 });
        }
        return true;
      }
    }
    return false;
  }
  isDelivery(messageId: number): boolean {
    const message = this.bus.messages.get(messageId);
    return message?.contact_id === MAGI_CONTACT_ID && this.bus.chats.get(message.chat_id)?.channel === "group";
  }
}
