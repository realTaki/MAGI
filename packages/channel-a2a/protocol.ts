import { validateAttachmentInputs, validateReaction, type Reaction, type AttachmentInput } from "@magi/bus";
import { createHash, createPublicKey } from "node:crypto";

/** MAGI-only chat subset inspired by A2A. conversationUUID has contextId semantics. */
export const PEER_PROTOCOL = "magi-peer/1";
export const MAX_BODY_BYTES = 15 * 1024 * 1024;
export const MAX_SYNC_BYTES = 16 * 1024 * 1024;
export type PeerIdentity = { publicKey: string; privateKey: string };
export type PeerMessage = {
  messageId: string;
  conversationUUID: string;
  content: string;
  reachableURL: string;
  handle?: string;
  replyTo?: string;
  requestReply?: boolean;
  attachments?: AttachmentInput[];
  reaction?: Reaction;
};
export type PeerReceipt = { messageId: string; conversationUUID: string; accepted: true; duplicate: boolean };
export type PeerCard = { protocol: typeof PEER_PROTOCOL; principal: string; publicKey: string; handle: string; reachableURL: string };
export type EnvelopeKind = "chat" | "receipt" | "sync" | "sync-result";
export class PeerError extends Error { constructor(message: string, readonly status = 400) { super(message); } }
export function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
export function principalFor(publicKey: string): string {
  const key = createPublicKey(publicKey);
  return createHash("sha256").update(key.export({ type: "spki", format: "der" })).digest("hex");
}
/** Identity labels route messages on a trusted private network; they are not authentication. */
export function encodeEnvelope<T>(identity: PeerIdentity, audience: string, kind: EnvelopeKind, data: T): string {
  return JSON.stringify({ protocol: PEER_PROTOCOL, sender: principalFor(identity.publicKey), recipient: audience, kind, data });
}
export function decodeEnvelope(body: string, audience: string, kind: EnvelopeKind, expectedPrincipal?: string): { principal: string; data: unknown } {
  let parsed: unknown;
  try { parsed = JSON.parse(body); } catch { throw new PeerError("invalid JSON"); }
  if (!record(parsed) || parsed.protocol !== PEER_PROTOCOL || parsed.kind !== kind || parsed.recipient !== audience || typeof parsed.sender !== "string" || !/^[0-9a-f]{64}$/.test(parsed.sender)) throw new PeerError("invalid peer message routing");
  if (expectedPrincipal && parsed.sender !== expectedPrincipal) throw new PeerError("unexpected reply sender");
  return { principal: parsed.sender, data: parsed.data };
}
export function parseMessage(value: unknown): PeerMessage {
  if (!record(value) || Object.keys(value).some(key => !["messageId", "conversationUUID", "content", "reachableURL", "handle", "replyTo", "attachments", "reaction", "requestReply"].includes(key))) throw new PeerError("only ordinary peer chat is supported");
  if (typeof value.messageId !== "string" || !value.messageId || value.messageId.length > 200 || typeof value.conversationUUID !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.conversationUUID) || typeof value.content !== "string" || value.content.length > 32_000 || typeof value.reachableURL !== "string") throw new PeerError("invalid peer chat");
  if (value.handle !== undefined && (typeof value.handle !== "string" || value.handle.length > 200)) throw new PeerError("invalid handle");
  if (value.replyTo !== undefined && (typeof value.replyTo !== "string" || !value.replyTo || value.replyTo.length > 200)) throw new PeerError("invalid reply reference");
  try { if (value.attachments !== undefined) validateAttachmentInputs(value.attachments); } catch { throw new PeerError("invalid peer attachments"); }
  if (value.requestReply !== undefined && typeof value.requestReply !== "boolean") throw new PeerError("invalid reply request");
  try { if (value.reaction !== undefined) validateReaction(value.reaction); } catch { throw new PeerError("invalid peer reaction"); }
  peerURL(value.reachableURL);
  return value as PeerMessage;
}
/** Deployment owns network trust; accept private HTTP routes and subnet hostnames. */
export function peerURL(value: string): URL {
  let url: URL;
  try { url = new URL(value); } catch { throw new PeerError("invalid peer URL"); }
  if ((url.protocol !== "https:" && url.protocol !== "http:") || url.username || url.password || url.search || url.hash || (url.pathname !== "/" && url.pathname !== "")) throw new PeerError("peer URL must be an HTTP or HTTPS origin");
  return url;
}
