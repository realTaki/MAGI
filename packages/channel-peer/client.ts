import { decodeEnvelope, encodeEnvelope, MAX_BODY_BYTES, MAX_SYNC_BYTES, PEER_PROTOCOL, PeerError, peerURL, principalFor, record, type PeerCard, type PeerIdentity, type PeerMessage, type PeerReceipt } from "./protocol.js";

async function boundedBody(response: Response, maximum = MAX_BODY_BYTES): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > maximum) throw new PeerError("peer response is too large");
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  return Buffer.concat(chunks).toString("utf8");
}
/** Read the peer card for a user-supplied private-network endpoint. */
export async function discoverPeer(endpoint: string): Promise<PeerCard> {
  const response = await fetch(new URL("/.well-known/magi-peer.json", peerURL(endpoint)), { redirect: "error", signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new PeerError(`peer discovery HTTP ${response.status}`, response.status);
  const card: unknown = JSON.parse(await boundedBody(response));
  if (!record(card) || card.protocol !== PEER_PROTOCOL || typeof card.publicKey !== "string" || typeof card.principal !== "string" || principalFor(card.publicKey) !== card.principal || typeof card.handle !== "string" || typeof card.reachableURL !== "string") throw new PeerError("invalid peer discovery");
  // Prevent a discovery server redirecting future delivery to another origin.
  if (peerURL(card.reachableURL).origin !== peerURL(endpoint).origin) throw new PeerError("peer discovery URL mismatch");
  return card as PeerCard;
}
export type GroupWire = { group: { event: import("@magi/bus/books/groupBook.js").SignedGroupEvent } };
export type SyncRequest = { conversationUUID: string; after?: number; afterEpoch?: number; memberships?: import("@magi/bus/books/groupBook.js").SignedGroupMembership[] };
export type SyncResponse = { memberships: import("@magi/bus/books/groupBook.js").SignedGroupMembership[]; events: import("@magi/bus/books/groupBook.js").SignedGroupEvent[]; nextCursor: number | null; moreMemberships: boolean; receivedEpoch: number };
export class PeerClient {
  readonly principal: string;
  constructor(private readonly identity: PeerIdentity) { this.principal = principalFor(identity.publicKey); }
  async sync(endpoint: string, recipient: string, request: SyncRequest): Promise<SyncResponse> {
    const body = encodeEnvelope(this.identity, recipient, "sync", request);
    if (Buffer.byteLength(body) > MAX_SYNC_BYTES) throw new PeerError("sync page is too large", 413);
    const response = await fetch(new URL("/sync", peerURL(endpoint)), { method: "POST", redirect: "error", headers: { "Content-Type": "application/json" }, body, signal: AbortSignal.timeout(10_000) });
    const text = await boundedBody(response, MAX_SYNC_BYTES);
    if (response.status !== 200) throw new PeerError(`peer sync HTTP ${response.status}`, response.status);
    const { data } = decodeEnvelope(text, this.principal, "sync-result", recipient);
    if (!record(data) || !Array.isArray(data.memberships) || data.memberships.length > 8 || !Array.isArray(data.events) || data.events.length > 8 || (data.nextCursor !== null && (!Number.isSafeInteger(data.nextCursor) || Number(data.nextCursor) < 0)) || typeof data.moreMemberships !== "boolean" || !Number.isSafeInteger(data.receivedEpoch)) throw new PeerError("invalid sync page");
    return data as SyncResponse;
  }
  async sendGroup(endpoint: string, recipient: string, group: GroupWire): Promise<PeerReceipt> {
    return this.sendMessage(endpoint, recipient, group, group.group.event.payload.eventId, group.group.event.payload.conversationUUID);
  }
  async send(endpoint: string, recipient: string, message: PeerMessage): Promise<PeerReceipt> {
    return this.sendMessage(endpoint, recipient, message, message.messageId, message.conversationUUID);
  }
  private async sendMessage(endpoint: string, recipient: string, input: PeerMessage | GroupWire, messageId: string, conversationUUID: string): Promise<PeerReceipt> {
    const body = encodeEnvelope(this.identity, recipient, "chat", input);
    if (Buffer.byteLength(body) > MAX_BODY_BYTES) throw new PeerError("chat is too large", 413);
    const response = await fetch(new URL("/chat", peerURL(endpoint)), { method: "POST", redirect: "error", headers: { "Content-Type": "application/json" }, body, signal: AbortSignal.timeout(10_000) });
    const text = await boundedBody(response);
    if (response.status !== 202) throw new PeerError(`peer chat HTTP ${response.status}`, response.status);
    const { data } = decodeEnvelope(text, this.principal, "receipt", recipient);
    if (!record(data) || data.accepted !== true || data.messageId !== messageId || data.conversationUUID !== conversationUUID || typeof data.duplicate !== "boolean") throw new PeerError("invalid peer receipt");
    return data as PeerReceipt;
  }
}
