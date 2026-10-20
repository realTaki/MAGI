import type { CardSpec } from "@magi/bus";

/** Renderer transport is the local owner bridge, never a central relay or remote owner API. */
export type PeerContact = {
  id: string;
  handle: string;
  name: string;
  nickname: string;
  url: string;
  local: boolean;
  online?: boolean;
  running?: boolean;
  group?: boolean;
  members?: Array<{ principal: string; endpoint: string }>;
};
export type PeerConversation = {
  id: string;
  title: string;
  members: Array<{ principal: string; endpoint: string }>;
  local: false;
  group: true;
};
export type PeerProfile = { handle: string; public_key: string | null; owner: boolean };
export type PeerAttachment = { id: string; name: string; mime_type: string; size: number; path: string };
export type PeerUpload = { name: string; mime_type: string; data_base64: string };
export type PeerReaction = { actor: string; emoji: string; mine?: boolean };
export type PeerMessageOptions = { reply_to?: number | string; attachments?: PeerUpload[] };
export type PeerEvent = {
  id: number | string;
  content: string;
  role: "user" | "assistant";
  metadata?: { card?: CardSpec; [key: string]: unknown };
  created_at: string;
  sender?: string;
  reply_to?: number | string;
  attachments?: PeerAttachment[];
  reactions?: PeerReaction[];
};

async function invoke<T>(method: string, payload?: unknown): Promise<T> {
  const bridge = window.magiDesktop?.invokeLocal;
  if (!bridge) throw new Error("The local Shell connection is unavailable");
  return await bridge(method, payload) as T;
}
export function listConversations(): Promise<PeerConversation[]> { return invoke("peer.conversations"); }
export function listContacts(): Promise<PeerContact[]> { return invoke("peer.contacts"); }
export function listPeerEvents(id: string): Promise<PeerEvent[]> { return invoke("peer.events", { id }); }
export function getPeerProfile(id: string): Promise<PeerProfile> { return invoke("peer.profile", { id }); }
export async function sendPeerMessage(id: string, content: string, metadata?: Record<string, unknown>, options?: PeerMessageOptions): Promise<void> {
  await invoke("peer.send", { id, content, ...(metadata ? { metadata } : {}), ...options });
}
export async function reactPeerMessage(id: string, message_id: number | string, emoji: string, remove = false): Promise<void> {
  await invoke("peer.react", { id, message_id, emoji, remove });
}
export function readPeerAttachment(id: string, message_id: number | string, attachment_id: string): Promise<PeerUpload> {
  return invoke("peer.attachment", { id, message_id, attachment_id });
}

/** Encrypt a card secret to the MAGI that asked for it. The plaintext stays in this form. */
export async function encryptSecret(publicKeyPem: string, plaintext: string): Promise<string> {
  const body = publicKeyPem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  const binary = atob(body);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  const key = await crypto.subtle.importKey(
    "spki",
    bytes.buffer,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["encrypt"],
  );
  const cipher = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, key, new TextEncoder().encode(plaintext));
  const out = new Uint8Array(cipher);
  let encoded = "";
  for (const byte of out) encoded += String.fromCharCode(byte);
  return btoa(encoded);
}

export type SourceStatus = {
  available: boolean;
  branch: string;
  /** Commit currently running from the local checkout. */
  commit: string;
  /** Latest commit reported by the checkout's origin branch. */
  latestCommit: string;
  /** Nearest release tag reachable from this checkout. */
  tag: string;
  /** GitHub owner/name for the checkout's origin, normally the operator's fork. */
  repository: string;
  /** GitHub owner/name used for the AgenticSociety comparison. */
  upstreamRepository: string;
  /** GitHub URL for latestCommit. */
  commitUrl: string;
  tagUrl: string;
  /** Commit where this checkout diverged from AgenticSociety. Empty when unknown. */
  forkPoint: string;
  forkPointUrl: string;
  remote: string;
  /** AgenticSociety has commits that are not in this checkout. */
  remoteAhead: boolean;
  /** False when the remote could not be read; local fields may still be set. */
  remoteChecked: boolean;
};

export async function getSourceStatus(): Promise<SourceStatus | null> {
  const invoke = window.magiDesktop?.invokeLocal;
  return invoke ? ((await invoke("source.status")) as SourceStatus) : null;
}
