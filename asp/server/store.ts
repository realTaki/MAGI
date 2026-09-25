/** ASP relay state, written through to its SQLite database. */

import { randomUUID } from "node:crypto";

import { and, eq, gt, notExists, sql } from "drizzle-orm";

import { LocalDatabase, type AspDb } from "../db/database.ts";
import { aspAgents } from "../db/tables/agents.ts";
import { aspChatKeys } from "../db/tables/chatKeys.ts";
import { aspChats } from "../db/tables/chats.ts";
import { aspEventAcks } from "../db/tables/eventAcks.ts";
import { aspEvents } from "../db/tables/events.ts";
import { aspMessageKeys } from "../db/tables/messageKeys.ts";
import { aspMessageRecipients } from "../db/tables/messageRecipients.ts";
import { aspParticipants } from "../db/tables/participants.ts";

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

function optionalText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function isParticipantStatus(value: unknown): value is ParticipantStatus {
  return value === "invited" || value === "joined" || value === "left";
}

function isChatState(value: unknown): value is ChatState {
  return value === "active" || value === "ended";
}

function isInboundPolicy(value: unknown): value is InboundPolicy {
  return value === "allowlist" || value === "open";
}

function agentFromJson(value: unknown): Agent {
  if (!isRecord(value) || typeof value.handle !== "string" || typeof value.token !== "string") {
    throw new Error("asp_agents record is not an agent");
  }
  const allowlist = new Set<string>();
  if (Array.isArray(value.allowlist)) {
    for (const entry of value.allowlist) {
      if (typeof entry === "string") {
        allowlist.add(entry);
      }
    }
  }
  return {
    handle: value.handle,
    token: value.token,
    name: optionalText(value.name),
    nickname: optionalText(value.nickname),
    inbound_policy: isInboundPolicy(value.inbound_policy) ? value.inbound_policy : "open",
    allowlist,
    managed: value.managed === true,
  };
}

function chatFromJson(value: unknown): Chat {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.creator !== "string" ||
    !isChatState(value.state) ||
    typeof value.created_at !== "number"
  ) {
    throw new Error("asp_chats record is not a chat");
  }
  return {
    id: value.id,
    creator: value.creator,
    state: value.state,
    topic: optionalText(value.topic),
    created_at: value.created_at,
    ended_at: typeof value.ended_at === "number" ? value.ended_at : null,
    description: optionalText(value.description),
    kind: optionalText(value.kind),
  };
}

function participantFromJson(value: unknown): Participant {
  if (!isRecord(value) || typeof value.handle !== "string" || !isParticipantStatus(value.status)) {
    throw new Error("asp_participants record is not a participant");
  }
  return {
    handle: value.handle,
    status: value.status,
    joined_at: typeof value.joined_at === "number" ? value.joined_at : null,
    left_at: typeof value.left_at === "number" ? value.left_at : null,
  };
}

function payloadFromJson(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error("event payload is not an object");
  }
  return value;
}

function agentRecord(agent: Agent): Record<string, unknown> {
  return {
    handle: agent.handle,
    token: agent.token,
    name: agent.name,
    nickname: agent.nickname,
    inbound_policy: agent.inbound_policy,
    allowlist: [...agent.allowlist].sort(),
    managed: agent.managed,
  };
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

  constructor(storage: LocalDatabase | null = null) {
    this.storage = storage;
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
    for (const row of db.select({ record: aspAgents.record_json }).from(aspAgents).all()) {
      const agent = agentFromJson(row.record);
      this.agents.set(agent.handle, agent);
      this.agentByToken.set(agent.token, agent.handle);
    }
    for (const row of db.select({ record: aspChats.record_json, next_sequence: aspChats.next_sequence }).from(aspChats).all()) {
      const chat = chatFromJson(row.record);
      this.chats.set(chat.id, chat);
      this.chatSeq.set(chat.id, row.next_sequence);
    }
    for (const row of db.select({ chat_id: aspParticipants.chat_id, record: aspParticipants.record_json }).from(aspParticipants).all()) {
      const participant = participantFromJson(row.record);
      this.participants.set(pair(row.chat_id, participant.handle), participant);
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
    db.transaction((tx) => {
      for (const eventId of eventIds) {
        const event = tx.select({ type: aspEvents.type }).from(aspEvents)
          .where(and(eq(aspEvents.event_id, eventId), eq(aspEvents.chat_id, chatId)))
          .get();
        if (event === undefined) {
          continue;
        }
        tx.insert(aspEventAcks).values({ event_id: eventId, handle }).onConflictDoNothing().run();
        if (event.type === "chat.message") {
          tx.update(aspMessageRecipients).set({ acked: true })
            .where(and(eq(aspMessageRecipients.event_id, eventId), eq(aspMessageRecipients.handle, handle)))
            .run();
        }
      }
      // A message event goes away only once no intended recipient is left unacked.
      // ASP is a relay, not the transcript — the desktop keeps the long-term copy,
      // and it is that write which makes its acknowledgment legal.
      const removable = tx.select({ event_id: aspEvents.event_id }).from(aspEvents)
        .where(and(
          eq(aspEvents.chat_id, chatId),
          eq(aspEvents.type, "chat.message"),
          notExists(tx.select({ one: sql`1` }).from(aspMessageRecipients).where(and(
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
        tx.delete(aspEvents).where(eq(aspEvents.event_id, eventId)).run();
      }
      // An invitation carries the message it was opened with; that message is gone.
      const removed = new Set(removable);
      for (const row of tx.select({ id: aspEvents.event_id, payload: aspEvents.payload_json }).from(aspEvents)
        .where(and(eq(aspEvents.chat_id, chatId), eq(aspEvents.type, "chat.invited")))
        .all()) {
        const initial = row.payload.initial_message;
        if (!isRecord(initial) || typeof initial.id !== "string" || !removed.has(initial.id)) {
          continue;
        }
        const payload = { ...row.payload };
        delete payload.initial_message;
        tx.update(aspEvents).set({ payload_json: payload }).where(eq(aspEvents.event_id, row.id)).run();
      }
    }, { behavior: "immediate" });
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
    const db = this.#db();
    if (db !== null) {
      db.transaction((tx) => {
        tx.insert(aspEvents).values({
          chat_id: chatId,
          sequence,
          event_id: event.event_id,
          type,
          created_at: event.created_at,
          payload_json: payload,
        }).run();
        if (type === "chat.message") {
          // Everyone who was in the room when it was said owes a receipt for it.
          const recipients = this.participantsIn(chatId)
            .filter((participant) => participant.status === "joined" || participant.status === "invited")
            .map((participant) => ({ event_id: event.event_id, handle: participant.handle }));
          if (recipients.length > 0) {
            tx.insert(aspMessageRecipients).values(recipients).run();
          }
        }
        const nextSequence = this.chatSeq.get(chatId);
        if (nextSequence === undefined) {
          throw new Error(chatId);
        }
        tx.update(aspChats).set({ next_sequence: nextSequence }).where(eq(aspChats.id, chatId)).run();
      }, { behavior: "immediate" });
    }
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
    const db = this.#db();
    if (db === null) {
      return;
    }
    const record = agentRecord(agent);
    db.insert(aspAgents).values({ handle: agent.handle, record_json: record })
      .onConflictDoUpdate({ target: aspAgents.handle, set: { record_json: record } })
      .run();
  }

  #saveChat(chat: Chat): void {
    const db = this.#db();
    if (db === null) {
      return;
    }
    const next_sequence = this.chatSeq.get(chat.id) ?? 0;
    db.insert(aspChats).values({ id: chat.id, record_json: chat, next_sequence })
      .onConflictDoUpdate({ target: aspChats.id, set: { record_json: chat, next_sequence } })
      .run();
  }

  #saveParticipant(chatId: string, participant: Participant): void {
    const db = this.#db();
    if (db === null) {
      return;
    }
    db.insert(aspParticipants)
      .values({ chat_id: chatId, handle: participant.handle, record_json: participant })
      .onConflictDoUpdate({
        target: [aspParticipants.chat_id, aspParticipants.handle],
        set: { record_json: participant },
      })
      .run();
  }

  #ensureUniqueToken(token: string): void {
    if (this.agentByToken.has(token)) {
      throw new Error("duplicate agent token in seed");
    }
  }
}
