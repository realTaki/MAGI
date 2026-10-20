import { constants, createHash, createPublicKey, sign, verify } from "node:crypto";

/** MAGI-only chat subset inspired by A2A. conversationUUID has contextId semantics. */
export const PEER_PROTOCOL = "magi-peer/1";
const DOMAIN = "MAGI direct peer chat v1\n";
export const MAX_BODY_BYTES = 256 * 1024;
export const MAX_SYNC_BYTES = 2 * 1024 * 1024;
export type PeerIdentity = { publicKey: string; privateKey: string };
export type PeerMessage = {
  messageId: string;
  conversationUUID: string;
  content: string;
  reachableURL: string;
  handle?: string;
  replyTo?: string;
};
export type PeerReceipt = { messageId: string; conversationUUID: string; accepted: true; duplicate: boolean };
export type PeerCard = { protocol: typeof PEER_PROTOCOL; principal: string; publicKey: string; handle: string; reachableURL: string };
export type EnvelopeKind = "chat" | "receipt" | "sync" | "sync-result" | "verify" | "verified";
type Payload<T> = { protocol: typeof PEER_PROTOCOL; audience: string; issuedAt: number; kind: EnvelopeKind; data: T };
type Envelope<T> = { publicKey: string; payload: Payload<T>; signature: string };
export class PeerError extends Error { constructor(message: string, readonly status = 400) { super(message); } }
export function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
export function principalFor(publicKey: string): string {
  const key = createPublicKey(publicKey);
  if (key.asymmetricKeyType !== "rsa" || (key.asymmetricKeyDetails?.modulusLength ?? 0) < 2048) throw new PeerError("RSA identity must be at least 2048 bits", 401);
  return createHash("sha256").update(key.export({ type: "spki", format: "der" })).digest("hex");
}
export function encodeEnvelope<T>(identity: PeerIdentity, audience: string, kind: EnvelopeKind, data: T): string {
  const payload: Payload<T> = { protocol: PEER_PROTOCOL, audience, issuedAt: Date.now(), kind, data };
  const signature = sign("sha256", Buffer.from(DOMAIN + JSON.stringify(payload)), { key: identity.privateKey, padding: constants.RSA_PKCS1_PSS_PADDING, saltLength: 32 }).toString("base64");
  return JSON.stringify({ publicKey: identity.publicKey, payload, signature });
}
export function decodeEnvelope(body: string, audience: string, kind: EnvelopeKind, expectedPrincipal?: string): { principal: string; data: unknown } {
  let parsed: unknown;
  try { parsed = JSON.parse(body); } catch { throw new PeerError("invalid JSON"); }
  if (!record(parsed) || typeof parsed.publicKey !== "string" || parsed.publicKey.length > 8192 || typeof parsed.signature !== "string" || parsed.signature.length > 2048 || !record(parsed.payload)) throw new PeerError("invalid signed envelope", 401);
  const envelope = parsed as unknown as Envelope<unknown>;
  const payload = envelope.payload;
  if (payload.protocol !== PEER_PROTOCOL || payload.kind !== kind || payload.audience !== audience || !Number.isSafeInteger(payload.issuedAt) || Math.abs(Date.now() - payload.issuedAt) > 5 * 60_000) throw new PeerError("invalid envelope scope or time", 401);
  let principal: string;
  try {
    principal = principalFor(envelope.publicKey);
    if (expectedPrincipal && principal !== expectedPrincipal) throw new Error("identity mismatch");
    if (!verify("sha256", Buffer.from(DOMAIN + JSON.stringify(payload)), { key: envelope.publicKey, padding: constants.RSA_PKCS1_PSS_PADDING, saltLength: 32 }, Buffer.from(envelope.signature, "base64"))) throw new Error("signature mismatch");
  } catch { throw new PeerError("peer authentication failed", 401); }
  return { principal, data: payload.data };
}
export function parseMessage(value: unknown): PeerMessage {
  if (!record(value) || Object.keys(value).some(key => !["messageId", "conversationUUID", "content", "reachableURL", "handle", "replyTo"].includes(key))) throw new PeerError("only ordinary peer chat is supported");
  if (typeof value.messageId !== "string" || !value.messageId || value.messageId.length > 200 || typeof value.conversationUUID !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.conversationUUID) || typeof value.content !== "string" || value.content.length > 32_000 || typeof value.reachableURL !== "string") throw new PeerError("invalid peer chat");
  if (value.handle !== undefined && (typeof value.handle !== "string" || value.handle.length > 200)) throw new PeerError("invalid handle");
  if (value.replyTo !== undefined && (typeof value.replyTo !== "string" || !value.replyTo || value.replyTo.length > 200)) throw new PeerError("invalid reply reference");
  peerURL(value.reachableURL);
  return value as PeerMessage;
}
/** Never follow advertised callbacks automatically. These checks apply at explicit use. */
export function peerURL(value: string): URL {
  let url: URL;
  try { url = new URL(value); } catch { throw new PeerError("invalid peer URL"); }
  const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) || url.username || url.password || url.search || url.hash || (url.pathname !== "/" && url.pathname !== "")) throw new PeerError("peer URL must be an HTTPS origin (HTTP only on loopback)");
  return url;
}
