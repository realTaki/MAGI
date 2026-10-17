/** ASP relay state, written through to its SQLite database. */

import { randomUUID } from "node:crypto";

import { transaction } from "@magi/bus/sqlite.js";
import { and, eq, gt, notExists, sql } from "drizzle-orm";

import { LocalDatabase, type AspDb } from "./db/database.js";
import { aspChatKeys } from "./db/tables/chatKeys.js";
import { aspEventAcks } from "./db/tables/eventAcks.js";
import { aspEvents } from "./db/tables/events.js";
import { aspMessageKeys } from "./db/tables/messageKeys.js";
import { aspMessageRecipients } from "./db/tables/messageRecipients.js";
import type { RelayWorker } from "./relayWorker.js";

export type ParticipantStatus = "invited" | "joined" | "left";
export type ChatState = "active" | "ended";
export type InboundPolicy = "allowlist" | "open";

export type Agent = {
  handle: string;
  token: string;
  name: string | null;
  nickname: string | null;
  inbound_policy: InboundPolicy;
  allowlist: Set<string>;
  managed: boolean;
  public_key: string | null;
};

export type Chat = {
  id: string;
  creator: string;
  state: ChatState;
  topic: string | null;
  created_at: number;
  ended_at: number | null;
  description: string | null;
  kind: string | null;
};

export type Participant = {
  handle: string;
  status: ParticipantStatus;
  joined_at: number | null;
  left_at: number | null;
};

export type ChatEvent = {
  type: string;
  event_id: string;
  created_at: number;
  payload: Record<string, unknown>;
  chat_id: string | null;
  sequence: number | null;
};

export type AgentSeed = {
  token: string;
  name?: string;
  inbound_policy?: InboundPolicy;
  allowlist?: string[];
};

export function makeId(prefix: string): string {
  return `${prefix}_${randomUUID().replaceAll("-", "").toUpperCase()}`;
}

export function nowMs(): number {
  return Date.now();
}

export function eventToWire(event: ChatEvent): Record<string, unknown> {
  const wire: Record<string, unknown> = {
    type: event.type,
    event_id: event.event_id,
    created_at: event.created_at,
    payload: event.payload,
  };
  if (event.chat_id !== null) {
    wire.chat_id = event.chat_id;
  }
  if (event.sequence !== null) {
    wire.sequence = event.sequence;
  }
  return wire;
}

function pair(left: string, right: string): string {
  return `${left}\0${right}`;
}

function triple(first: string, second: string, third: string): string {
  return `${first}\0${second}\0${third}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isInboundPolicy(value: unknown): value is InboundPolicy {
  return value === "allowlist" || value === "open";
}

function payloadFromJson(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error("event payload is not an object");
  }
  return value;
}

export class Store {
  readonly agents = new Map<string, Agent>();
  readonly agentByToken = new Map<string, string>();
  readonly chats = new Map<string, Chat>();
  readonly participants = new Map<string, Participant>();
  readonly chatSeq = new Map<string, number>();
  readonly idempotency = new Map<string, [string, number]>();
  readonly chatIdempotency = new Map<string, [string, number | null]>();
  readonly storage: LocalDatabase | null;
  readonly relay: RelayWorker | null;

  constructor(storage: LocalDatabase | null = null, relay: RelayWorker | null = null) {
    this.storage = storage;
    this.relay = relay;
  }

  #db(): AspDb | null {
    return this.storage?.db ?? null;
  }

  activate(seed: Record<string, string | AgentSeed> = {}): void {
    const db = this.#db();
    if (db === null) {
      this.seedAgents(seed);
      return;
    }
    this.agents.clear();
    this.agentByToken.clear();
    this.chats.clear();
    this.participants.clear();
    this.chatSeq.clear();
    this.idempotency.clear();
    this.chatIdempotency.clear();
    const directory = this.relay?.loadDirectory();
    if (directory) {
      for (const agent of directory.agents) {
        this.agents.set(agent.handle, agent);
        this.agentByToken.set(agent.token, agent.handle);
      }
      for (const chat of directory.chats) {
        this.chats.set(chat.id, chat);
      }
      for (const [chatId, sequence] of directory.chatSeq) {
        this.chatSeq.set(chatId, sequence);
      }
      for (const { chatId, participant } of directory.participants) {
        this.participants.set(pair(chatId, participant.handle), participant);
      }
    }
    for (const row of db.select().from(aspMessageKeys).all()) {
      this.idempotency.set(triple(row.chat_id, row.sender, row.key), [row.message_id, row.sequence]);
    }
    for (const row of db.select().from(aspChatKeys).all()) {
      this.chatIdempotency.set(pair(row.creator, row.key), [row.chat_id, row.sequence ?? null]);
    }
    this.seedAgents(seed);
  }

  updateAgent(agent: Agent): void {
    this.#saveAgent(agent);
  }

  updateChat(chat: Chat): void {
    this.#saveChat(chat);
  }

  updateEvent(event: ChatEvent): void {
    const db = this.#db();
    if (db === null) {
      return;
    }
    db.update(aspEvents)
      .set({ payload_json: event.payload })
      .where(eq(aspEvents.event_id, event.event_id))
      .run();
  }

  acknowledge(handle: string, chatId: string, eventIds: string[]): void {
    if (this.getParticipant(chatId, handle) === undefined) {
      throw new Error(chatId);
    }
    const db = this.#db();
    if (db === null) {
      return;
    }
    transaction(db.$client, () => {
      for (const eventId of eventIds) {
        const event = db.select({ type: aspEvents.type }).from(aspEvents)
          .where(and(eq(aspEvents.event_id, eventId), eq(aspEvents.chat_id, chatId)))
          .get();
        if (event === undefined) {
          continue;
        }
        db.insert(aspEventAcks).values({ event_id: eventId, handle }).onConflictDoNothing().run();
        if (event.type === "chat.message") {
          db.update(aspMessageRecipients).set({ acked: true })
            .where(and(eq(aspMessageRecipients.event_id, eventId), eq(aspMessageRecipients.handle, handle)))
            .run();
        }
      }
      // A message event goes away only once no intended recipient is left unacked.
      // ASP is a relay, not the transcript — the desktop keeps the long-term copy,
      // and it is that write which makes its acknowledgment legal.
      const removable = db.select({ event_id: aspEvents.event_id }).from(aspEvents)
        .where(and(
          eq(aspEvents.chat_id, chatId),
          eq(aspEvents.type, "chat.message"),
          notExists(db.select({ one: sql`1` }).from(aspMessageRecipients).where(and(
            eq(aspMessageRecipients.event_id, aspEvents.event_id),
            eq(aspMessageRecipients.acked, false),
          ))),
        ))
        .all()
        .map((row) => row.event_id);
      if (removable.length === 0) {
        return;
      }
      for (const eventId of removable) {
        db.delete(aspEvents).where(eq(aspEvents.event_id, eventId)).run();
      }
      // An invitation carries the message it was opened with; that message is gone.
      const removed = new Set(removable);
      for (const row of db.select({ id: aspEvents.event_id, payload: aspEvents.payload_json }).from(aspEvents)
        .where(and(eq(aspEvents.chat_id, chatId), eq(aspEvents.type, "chat.invited")))
        .all()) {
        const initial = row.payload.initial_message;
        if (!isRecord(initial) || typeof initial.id !== "string" || !removed.has(initial.id)) {
          continue;
        }
        const payload = { ...row.payload };
        delete payload.initial_message;
        db.update(aspEvents).set({ payload_json: payload }).where(eq(aspEvents.event_id, row.id)).run();
      }
    }, "immediate");
  }

  isAcknowledged(handle: string, eventId: string): boolean {
    const db = this.#db();
    if (db === null) {
      return false;
    }
    return db.select({ one: sql`1` }).from(aspEventAcks)
      .where(and(eq(aspEventAcks.event_id, eventId), eq(aspEventAcks.handle, handle)))
      .get() !== undefined;
  }

  seedAgents(seed: Record<string, string | AgentSeed>): void {
    for (const [handle, config] of Object.entries(seed)) {
      if (typeof config === "string") {
        this.registerAgent({ handle, token: config });
        continue;
      }
      const policy = config.inbound_policy ?? "open";
      if (!isInboundPolicy(policy)) {
        throw new Error(
          `unknown inbound_policy ${JSON.stringify(policy)} for ${handle}; expected 'allowlist' or 'open'`,
        );
      }
      const agent = this.registerAgent({ handle, token: config.token, name: config.name ?? null });
      agent.inbound_policy = policy;
      agent.allowlist = new Set(config.allowlist ?? []);
      this.#saveAgent(agent);
    }
  }

  getAgent(handle: string): Agent | undefined {
    return this.agents.get(handle);
  }

  authenticate(token: string): Agent | undefined {
    const handle = this.agentByToken.get(token);
    return handle === undefined ? undefined : this.agents.get(handle);
  }

  registerAgent(input: {
    handle: string;
    token: string;
    name?: string | null;
    managed?: boolean;
  }): Agent {
    const name = input.name ?? null;
    const managed = input.managed ?? false;
    const existing = this.agents.get(input.handle);
    if (existing !== undefined) {
      if (existing.token !== input.token) {
        this.agentByToken.delete(existing.token);
        this.#ensureUniqueToken(input.token);
        existing.token = input.token;
        this.agentByToken.set(input.token, input.handle);
      }
      if (name !== null) {
        existing.name = name;
      }
      if (managed) {
        existing.managed = true;
      }
      this.#saveAgent(existing);
      return existing;
    }
    this.#ensureUniqueToken(input.token);
    const agent: Agent = {
      handle: input.handle,
      token: input.token,
      name,
      nickname: null,
      inbound_policy: "open",
      allowlist: new Set(),
      managed,
      public_key: null,
    };
    this.agents.set(input.handle, agent);
    this.agentByToken.set(input.token, input.handle);
    this.#saveAgent(agent);
    return agent;
  }

  static magiHandle(name: string): string {
    return `@${name}.magi`;
  }

  nextMagiName(): string {
    let index = 0;
    for (;;) {
      const name = `eva-${String(index).padStart(3, "0")}`;
      if (!this.agents.has(Store.magiHandle(name))) {
        return name;
      }
      index += 1;
    }
  }

  createChat(input: {
    creator: string;
    topic: string | null;
    kind?: string | null;
    description?: string | null;
  }): Chat {
    const chat: Chat = {
      id: makeId("sess"),
      creator: input.creator,
      state: "active",
      topic: input.topic,
      created_at: nowMs(),
      ended_at: null,
      description: input.description ?? null,
      kind: input.kind ?? null,
    };
    this.chats.set(chat.id, chat);
    this.chatSeq.set(chat.id, 0);
    this.#saveChat(chat);
    return chat;
  }

  getChat(chatId: string): Chat | undefined {
    return this.chats.get(chatId);
  }

  endChat(chatId: string): void {
    const chat = this.chats.get(chatId);
    if (chat === undefined) {
      throw new Error(chatId);
    }
    chat.state = "ended";
    chat.ended_at = nowMs();
    this.#saveChat(chat);
  }

  reopenChat(chatId: string): void {
    const chat = this.chats.get(chatId);
    if (chat === undefined) {
      throw new Error(chatId);
    }
    chat.state = "active";
    chat.ended_at = null;
    this.#saveChat(chat);
  }

  addParticipant(chatId: string, handle: string, status: ParticipantStatus): Participant {
    const participant: Participant = {
      handle,
      status,
      joined_at: status === "joined" ? nowMs() : null,
      left_at: null,
    };
    this.participants.set(pair(chatId, handle), participant);
    this.#saveParticipant(chatId, participant);
    return participant;
  }

  getParticipant(chatId: string, handle: string): Participant | undefined {
    return this.participants.get(pair(chatId, handle));
  }

  participantsIn(chatId: string): Participant[] {
    const found: Participant[] = [];
    for (const [key, participant] of this.participants) {
      if (key.startsWith(`${chatId}\0`)) {
        found.push(participant);
      }
    }
    return found;
  }

  setStatus(chatId: string, handle: string, status: ParticipantStatus): void {
    const participant = this.participants.get(pair(chatId, handle));
    if (participant === undefined) {
      throw new Error(`${chatId} ${handle}`);
    }
    participant.status = status;
    if (status === "joined" && participant.joined_at === null) {
      participant.joined_at = nowMs();
    }
    if (status === "left") {
      participant.left_at = nowMs();
    }
    this.#saveParticipant(chatId, participant);
  }

  appendChatEvent(chatId: string, type: string, payload: Record<string, unknown>): ChatEvent {
    const sequence = this.chatSeq.get(chatId);
    if (sequence === undefined) {
      throw new Error(chatId);
    }
    this.chatSeq.set(chatId, sequence + 1);
    const event: ChatEvent = {
      type,
      event_id: makeId("evt"),
      created_at: nowMs(),
      payload,
      chat_id: chatId,
      sequence,
    };
    const recipients = type === "chat.message"
      ? this.participantsIn(chatId)
        .filter((participant) => participant.status === "joined" || participant.status === "invited")
        .map((participant) => participant.handle)
      : [];
    this.relay?.recordEvent(event, recipients);
    return event;
  }

  chatEventsAfter(
    chatId: string,
    afterSequence: number | null,
    limit: number | null,
  ): ChatEvent[] {
    const db = this.#db();
    if (db === null) {
      return [];
    }
    const selected = db.select({
      type: aspEvents.type,
      event_id: aspEvents.event_id,
      created_at: aspEvents.created_at,
      payload: aspEvents.payload_json,
      chat_id: aspEvents.chat_id,
      sequence: aspEvents.sequence,
    }).from(aspEvents)
      .where(afterSequence === null
        ? eq(aspEvents.chat_id, chatId)
        : and(eq(aspEvents.chat_id, chatId), gt(aspEvents.sequence, afterSequence)))
      .orderBy(aspEvents.sequence);
    const rows = limit === null ? selected.all() : selected.limit(limit).all();
    return rows.map((row) => ({
      type: row.type,
      event_id: row.event_id,
      created_at: row.created_at,
      payload: payloadFromJson(row.payload),
      chat_id: row.chat_id,
      sequence: row.sequence,
    }));
  }

  eventsForChat(chatId: string): ChatEvent[] {
    return this.chatEventsAfter(chatId, null, null);
  }

  getIdempotentMessage(chatId: string, sender: string, key: string): [string, number] | undefined {
    return this.idempotency.get(triple(chatId, sender, key));
  }

  recordIdempotentMessage(
    chatId: string,
    sender: string,
    key: string,
    messageId: string,
    sequence: number,
  ): void {
    this.idempotency.set(triple(chatId, sender, key), [messageId, sequence]);
    const db = this.#db();
    if (db !== null) {
      db.insert(aspMessageKeys).values({ chat_id: chatId, sender, key, message_id: messageId, sequence })
        .onConflictDoUpdate({
          target: [aspMessageKeys.chat_id, aspMessageKeys.sender, aspMessageKeys.key],
          set: { message_id: messageId, sequence },
        })
        .run();
    }
  }

  getIdempotentChat(creator: string, key: string): [string, number | null] | undefined {
    return this.chatIdempotency.get(pair(creator, key));
  }

  recordIdempotentChat(
    creator: string,
    key: string,
    chatId: string,
    sequence: number | null,
  ): void {
    this.chatIdempotency.set(pair(creator, key), [chatId, sequence]);
    const db = this.#db();
    if (db !== null) {
      db.insert(aspChatKeys).values({ creator, key, chat_id: chatId, sequence })
        .onConflictDoUpdate({
          target: [aspChatKeys.creator, aspChatKeys.key],
          set: { chat_id: chatId, sequence },
        })
        .run();
    }
  }

  #saveAgent(agent: Agent): void {
    this.relay?.savePrincipal(agent);
  }

  #saveChat(chat: Chat): void {
    this.relay?.saveChat(chat, this.chatSeq.get(chat.id) ?? 0);
  }

  #saveParticipant(chatId: string, participant: Participant): void {
    this.relay?.saveParticipant(chatId, participant);
  }

  #ensureUniqueToken(token: string): void {
    if (this.agentByToken.has(token)) {
      throw new Error("duplicate agent token in seed");
    }
  }
}
