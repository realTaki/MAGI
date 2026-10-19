import { decodeEnvelope, encodeEnvelope, MAX_BODY_BYTES, PEER_PROTOCOL, PeerError, peerURL, principalFor, record, type PeerCard, type PeerIdentity, type PeerMessage, type PeerReceipt } from "./protocol.js";

async function boundedBody(response: Response): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > MAX_BODY_BYTES) throw new PeerError("peer response is too large");
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  return Buffer.concat(chunks).toString("utf8");
}
/** Discovery does not grant authority or save a contact. Only explicit tools do that. */
export async function discoverPeer(endpoint: string): Promise<PeerCard> {
  const response = await fetch(new URL("/.well-known/magi-peer.json", peerURL(endpoint)), { redirect: "error", signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new PeerError(`peer discovery HTTP ${response.status}`, response.status);
  const card: unknown = JSON.parse(await boundedBody(response));
  if (!record(card) || card.protocol !== PEER_PROTOCOL || typeof card.publicKey !== "string" || typeof card.principal !== "string" || principalFor(card.publicKey) !== card.principal || typeof card.handle !== "string" || typeof card.reachableURL !== "string") throw new PeerError("invalid peer discovery");
  // Prevent a discovery server redirecting future delivery to another origin.
  if (peerURL(card.reachableURL).origin !== peerURL(endpoint).origin) throw new PeerError("peer discovery URL mismatch");
  return card as PeerCard;
}
export class PeerClient {
  readonly principal: string;
  constructor(private readonly identity: PeerIdentity) { this.principal = principalFor(identity.publicKey); }
  async send(endpoint: string, recipient: string, message: PeerMessage): Promise<PeerReceipt> {
    const body = encodeEnvelope(this.identity, recipient, "chat", message);
    if (Buffer.byteLength(body) > MAX_BODY_BYTES) throw new PeerError("chat is too large", 413);
    const response = await fetch(new URL("/chat", peerURL(endpoint)), { method: "POST", redirect: "error", headers: { "Content-Type": "application/json" }, body, signal: AbortSignal.timeout(10_000) });
    const text = await boundedBody(response);
    if (response.status !== 202) throw new PeerError(`peer chat HTTP ${response.status}`, response.status);
    const { data } = decodeEnvelope(text, this.principal, "receipt", recipient);
    if (!record(data) || data.accepted !== true || data.messageId !== message.messageId || data.conversationUUID !== message.conversationUUID || typeof data.duplicate !== "boolean") throw new PeerError("invalid peer receipt");
    return data as PeerReceipt;
  }
}
