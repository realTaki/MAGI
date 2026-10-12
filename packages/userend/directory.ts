/**
 * Operator chat directory on the BUS: ChatBook + ContactBook + ChatMemberBook.
 *
 * The Electron table still speaks ASP's chat_id JSON. kind/name/description/created_at
 * have no Book column, so they sit in ChatBook.info as JSON. Members are a set.
 */

import type { Bus } from "@magi/bus";

export const ASP_CHANNEL = "asp";

export type OperatorChat = {
  chat_id: string;
  kind?: "bot" | "group";
  agents: string[];
  participants?: Array<{ handle: string; status: "invited" | "joined" | "left" }>;
  name?: string;
  topic?: string;
  description?: string;
  created_at?: number;
};

type ChatExtras = {
  kind?: "bot" | "group";
  name?: string;
  description?: string;
  created_at?: number;
};

export function saveChats(bus: Bus, rows: unknown[]): void {
  for (const row of rows) {
    const chat = asChat(row);
    if (chat === null) continue;
    const record = bus.chats.forChannel(ASP_CHANNEL, chat.chat_id);
    bus.chats.setTopic(record.id, chat.topic ?? "");
    bus.chats.setInfo(record.id, JSON.stringify(extras(chat)));
    syncMembers(bus, record.id, chat);
  }
}

export function listChats(bus: Bus): OperatorChat[] {
  const rows = bus.chats.list(ASP_CHANNEL).map((record) => {
    const extra = parseExtras(record.info);
    const members = bus.chatMembers.list(record.id);
    const handles = members
      .map((contact) => contact.asp_handle)
      .filter((handle): handle is string => typeof handle === "string" && handle !== "");
    const agents = members
      .filter((contact) => contact.role === "magi" && contact.asp_handle && contact.asp_handle !== bus.handle)
      .map((contact) => contact.asp_handle)
      .filter((handle): handle is string => typeof handle === "string");
    const kind = extra.kind ?? (agents.length === 1 ? "bot" : "group");
    const name = extra.name ?? (kind === "bot" ? magiName(members, agents) : undefined);
    return {
      chat_id: record.delivery_address,
      kind,
      agents,
      participants: handles.map((handle) => ({ handle, status: "joined" as const })),
      name,
      topic: record.topic || undefined,
      description: extra.description,
      created_at: extra.created_at,
    };
  });
  rows.sort((left, right) => (right.created_at ?? 0) - (left.created_at ?? 0));
  return rows;
}

export function ackThrough(bus: Bus, chatId: string): number {
  return bus.chats.read(ASP_CHANNEL, chatId);
}

export function markAcknowledged(bus: Bus, chatId: string, sequence: number): void {
  bus.chats.markRead(ASP_CHANNEL, chatId, sequence);
}

function syncMembers(bus: Bus, chatId: number, chat: OperatorChat): void {
  const wanted = new Set(presentHandles(chat));
  for (const contact of bus.chatMembers.list(chatId)) {
    if (contact.asp_handle && !wanted.has(contact.asp_handle)) bus.chatMembers.remove(chatId, contact.id);
  }
  for (const handle of wanted) {
    bus.chatMembers.add(chatId, bus.contacts.forAspHandle(handle).id);
  }
  if (chat.kind === "bot" && chat.name && chat.agents.length === 1) {
    const contact = bus.contacts.forAspHandle(chat.agents[0]);
    if (contact.nickname !== chat.name) bus.contacts.update(contact.id, { nickname: chat.name });
  }
}

function presentHandles(chat: OperatorChat): string[] {
  if (chat.participants && chat.participants.length > 0) {
    return [...new Set(
      chat.participants
        .filter((participant) => participant.status === "invited" || participant.status === "joined")
        .map((participant) => participant.handle.trim())
        .filter((handle) => handle !== ""),
    )];
  }
  return [...new Set(["@user.magi", ...chat.agents].map((handle) => handle.trim()).filter((handle) => handle !== ""))];
}

function extras(chat: OperatorChat): ChatExtras {
  return {
    kind: chat.kind,
    name: chat.name,
    description: chat.description,
    created_at: chat.created_at,
  };
}

function parseExtras(info: string): ChatExtras {
  if (info === "") return {};
  try {
    const value: unknown = JSON.parse(info);
    if (!value || typeof value !== "object") return { description: info };
    const kind = "kind" in value && (value.kind === "bot" || value.kind === "group") ? value.kind : undefined;
    const name = "name" in value && typeof value.name === "string" ? value.name : undefined;
    const description = "description" in value && typeof value.description === "string" ? value.description : undefined;
    const created_at = "created_at" in value && typeof value.created_at === "number" ? value.created_at : undefined;
    return { kind, name, description, created_at };
  } catch {
    return { description: info };
  }
}

function magiName(members: { asp_handle: string | null; nickname: string | null; name: string }[], agents: string[]): string | undefined {
  const handle = agents[0];
  if (!handle) return undefined;
  const contact = members.find((member) => member.asp_handle === handle);
  return contact?.nickname || contact?.name || handle;
}

function asChat(value: unknown): OperatorChat | null {
  if (typeof value !== "object" || value === null) return null;
  const chat_id = "chat_id" in value && typeof value.chat_id === "string" ? value.chat_id : "";
  if (chat_id === "") return null;
  const kind = "kind" in value && (value.kind === "bot" || value.kind === "group") ? value.kind : undefined;
  const agents = "agents" in value && Array.isArray(value.agents)
    ? value.agents.filter((handle): handle is string => typeof handle === "string")
    : [];
  const participants = "participants" in value && Array.isArray(value.participants)
    ? value.participants.flatMap((entry) => {
      if (typeof entry !== "object" || entry === null) return [];
      const handle = "handle" in entry && typeof entry.handle === "string" ? entry.handle : "";
      const status = "status" in entry && (entry.status === "invited" || entry.status === "joined" || entry.status === "left")
        ? entry.status : null;
      return handle && status ? [{ handle, status }] : [];
    })
    : undefined;
  const name = "name" in value && typeof value.name === "string" ? value.name : undefined;
  const topic = "topic" in value && typeof value.topic === "string" ? value.topic : undefined;
  const description = "description" in value && typeof value.description === "string" ? value.description : undefined;
  const created_at = "created_at" in value && typeof value.created_at === "number" ? value.created_at : undefined;
  return { chat_id, kind, agents, participants, name, topic, description, created_at };
}
