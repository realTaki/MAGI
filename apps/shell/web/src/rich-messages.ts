import type { ChatMessage } from "./chat-model.ts";
import type { PeerReaction, PeerUpload } from "./peer.ts";

/** Keep these limits in sync with the owner bridge. Validate before reading bytes. */
export const MAX_ATTACHMENTS = 8;
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const REACTION_EMOJI = ["👍", "❤️", "🎉", "😂", "👀", "🙏"];

export function validateFiles(files: readonly Pick<File, "size">[]): "attachmentCount" | "attachmentTotal" | undefined {
  if (files.length > MAX_ATTACHMENTS) return "attachmentCount";
  if (files.reduce((total, file) => total + file.size, 0) > MAX_UPLOAD_BYTES) return "attachmentTotal";
}

export async function encodeAttachment(file: File): Promise<PeerUpload> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return { name: file.name, mime_type: file.type || "application/octet-stream", data_base64: btoa(binary) };
}

export function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function messageSummary(message: ChatMessage | undefined): string {
  if (!message) return "";
  const text = "text" in message ? message.text.trim() : "";
  return (text || message.attachments?.map((attachment) => attachment.name).join(", ") || "").slice(0, 180);
}

export function replyMessage(messages: readonly ChatMessage[], id: string | number): ChatMessage | undefined {
  return messages.find((message) => message.id !== undefined && String(message.id) === String(id));
}

export function groupedReactions(reactions: readonly PeerReaction[] = []): Array<{ emoji: string; actors: string[]; mine: boolean }> {
  const grouped = new Map<string, { actors: Set<string>; mine: boolean }>();
  for (const { emoji, actor, mine } of reactions) {
    if (!grouped.has(emoji)) grouped.set(emoji, { actors: new Set(), mine: false });
    const group = grouped.get(emoji)!;
    group.actors.add(actor);
    group.mine ||= mine === true;
  }
  return [...grouped].map(([emoji, group]) => ({ emoji, actors: [...group.actors], mine: group.mine }));
}
