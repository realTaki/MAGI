/** ASP relay state, written through to its SQLite database. */

import { randomUUID } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";

import { LocalDatabase } from "../db/database.ts";
import { transaction } from "../db/versions.ts";

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

type SqlValue = string | number | bigint | Uint8Array | null;
type SqlRow = Record<string, SqlValue>;

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

function parseJson(text: string): unknown {
  return JSON.parse(text);
}

function isSqlRow(value: unknown): value is SqlRow {
  return isRecord(value);
}

function textColumn(row: SqlRow, key: string): string {
  const value = row[key];
  if (typeof value !== "string") {
    throw new Error(`expected text column ${key}`);
  }
  return value;
}

function numberColumn(row: SqlRow, key: string): number {
  const value = row[key];
  if (typeof value === "number") {
    return value;
  }
  if (typeof value === "bigint") {
    return Number(value);
  }
  throw new Error(`expected integer column ${key}`);
}

function nullableNumber(row: SqlRow, key: string): number | null {
  const value = row[key];
  if (value === null || value === undefined) {
    return null;
  }
  return numberColumn(row, key);
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

  #db(): DatabaseSync | null {
    return this.storage?.connection ?? null;
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
    for (const row of this.#rows(db, "SELECT record_json FROM asp_agents")) {
      const agent = agentFromJson(parseJson(textColumn(row, "record_json")));
      this.agents.set(agent.handle, agent);
      this.agentByToken.set(agent.token, agent.handle);
    }
    for (const row of this.#rows(db, "SELECT record_json, next_sequence FROM asp_chats")) {
      const chat = chatFromJson(parseJson(textColumn(row, "record_json")));
      this.chats.set(chat.id, chat);
      this.chatSeq.set(chat.id, numberColumn(row, "next_sequence"));
    }
    for (const row of this.#rows(db, "SELECT chat_id, record_json FROM asp_participants")) {
      const participant = participantFromJson(parseJson(textColumn(row, "record_json")));
      this.participants.set(pair(textColumn(row, "chat_id"), participant.handle), participant);
    }
    for (const row of this.#rows(db, "SELECT * FROM asp_message_keys")) {
      this.idempotency.set(
        triple(textColumn(row, "chat_id"), textColumn(row, "sender"), textColumn(row, "key")),
        [textColumn(row, "message_id"), numberColumn(row, "sequence")],
      );
    }
    for (const row of this.#rows(db, "SELECT * FROM asp_chat_keys")) {
      this.chatIdempotency.set(pair(textColumn(row, "creator"), textColumn(row, "key")), [
        textColumn(row, "chat_id"),
        nullableNumber(row, "sequence"),
      ]);
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
    transaction(db, () => {
      db.prepare("UPDATE asp_events SET payload_json = ? WHERE event_id = ?").run(
        JSON.stringify(event.payload),
        event.event_id,
      );
    });
  }

  acknowledge(handle: string, chatId: string, eventIds: string[]): void {
    if (this.getParticipant(chatId, handle) === undefined) {
      throw new Error(chatId);
    }
    const db = this.#db();
    if (db === null) {
      return;
    }
    transaction(db, () => {
      const lookup = db.prepare(
        "SELECT type FROM asp_events WHERE event_id = ? AND chat_id = ?",
      );
      const ack = db.prepare("INSERT OR IGNORE INTO asp_event_acks VALUES (?, ?)");
      const mark = db.prepare(
        "UPDATE asp_message_recipients SET acked = 1 WHERE event_id = ? AND handle = ?",
      );
      for (const eventId of eventIds) {
        const row = lookup.get(eventId, chatId);
        if (!isSqlRow(row)) {
          continue;
        }
        ack.run(eventId, handle);
        if (textColumn(row, "type") === "chat.message") {
          mark.run(eventId, handle);
        }
      }
      // A message event goes away only once no intended recipient is left unacked.
      // ASP is a relay, not the transcript — the desktop keeps the long-term copy,
      // and it is that write which makes its acknowledgment legal.
      const removable = this.#rows(
        db,
        `SELECT e.event_id FROM asp_events e
         WHERE e.chat_id = ? AND e.type = 'chat.message'
           AND NOT EXISTS (
             SELECT 1 FROM asp_message_recipients r
             WHERE r.event_id = e.event_id AND r.acked = 0
           )`,
        chatId,
      ).map((row) => textColumn(row, "event_id"));
      if (removable.length === 0) {
        return;
      }
      const remove = db.prepare("DELETE FROM asp_events WHERE event_id = ?");
      for (const eventId of removable) {
        remove.run(eventId);
      }
      const removed = new Set(removable);
      const invited = db.prepare(
        "SELECT event_id, payload_json FROM asp_events WHERE chat_id = ? AND type = 'chat.invited'",
      );
      const rewrite = db.prepare("UPDATE asp_events SET payload_json = ? WHERE event_id = ?");
      for (const row of invited.all(chatId)) {
        if (!isSqlRow(row)) {
          continue;
        }
        const payload = payloadFromJson(parseJson(textColumn(row, "payload_json")));
        const initial = payload.initial_message;
        if (
          isRecord(initial) &&
          typeof initial.id === "string" &&
          removed.has(initial.id)
        ) {
          delete payload.initial_message;
          rewrite.run(JSON.stringify(payload), textColumn(row, "event_id"));
        }
      }
    });
  }

  isAcknowledged(handle: string, eventId: string): boolean {
    const db = this.#db();
    if (db === null) {
      return false;
    }
    return (
      db.prepare("SELECT 1 FROM asp_event_acks WHERE event_id = ? AND handle = ?").get(eventId, handle) !==
      undefined
    );
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
      transaction(db, () => {
        db.prepare("INSERT INTO asp_events VALUES (?, ?, ?, ?, ?, ?)").run(
          chatId,
          sequence,
          event.event_id,
          type,
          event.created_at,
          JSON.stringify(payload),
        );
        if (type === "chat.message") {
          const insert = db.prepare(
            "INSERT INTO asp_message_recipients (event_id, handle) VALUES (?, ?)",
          );
          for (const participant of this.participantsIn(chatId)) {
            if (participant.status === "joined" || participant.status === "invited") {
              insert.run(event.event_id, participant.handle);
            }
          }
        }
        const nextSequence = this.chatSeq.get(chatId);
        if (nextSequence === undefined) {
          throw new Error(chatId);
        }
        db.prepare("UPDATE asp_chats SET next_sequence = ? WHERE id = ?").run(nextSequence, chatId);
      });
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
    let query = "SELECT * FROM asp_events WHERE chat_id = ?";
    const params: Array<string | number> = [chatId];
    if (afterSequence !== null) {
      query += " AND sequence > ?";
      params.push(afterSequence);
    }
    query += " ORDER BY sequence";
    if (limit !== null) {
      query += " LIMIT ?";
      params.push(limit);
    }
    return this.#rows(db, query, ...params).map((row) => ({
      type: textColumn(row, "type"),
      event_id: textColumn(row, "event_id"),
      created_at: numberColumn(row, "created_at"),
      payload: payloadFromJson(parseJson(textColumn(row, "payload_json"))),
      chat_id: textColumn(row, "chat_id"),
      sequence: numberColumn(row, "sequence"),
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
      transaction(db, () => {
        db.prepare("INSERT OR REPLACE INTO asp_message_keys VALUES (?, ?, ?, ?, ?)").run(
          chatId,
          sender,
          key,
          messageId,
          sequence,
        );
      });
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
      transaction(db, () => {
        db.prepare("INSERT OR REPLACE INTO asp_chat_keys VALUES (?, ?, ?, ?)").run(
          creator,
          key,
          chatId,
          sequence,
        );
      });
    }
  }

  #saveAgent(agent: Agent): void {
    const db = this.#db();
    if (db === null) {
      return;
    }
    transaction(db, () => {
      db.prepare("INSERT OR REPLACE INTO asp_agents VALUES (?, ?)").run(
        agent.handle,
        JSON.stringify(agentRecord(agent)),
      );
    });
  }

  #saveChat(chat: Chat): void {
    const db = this.#db();
    if (db === null) {
      return;
    }
    transaction(db, () => {
      db.prepare("INSERT OR REPLACE INTO asp_chats VALUES (?, ?, ?)").run(
        chat.id,
        JSON.stringify(chat),
        this.chatSeq.get(chat.id) ?? 0,
      );
    });
  }

  #saveParticipant(chatId: string, participant: Participant): void {
    const db = this.#db();
    if (db === null) {
      return;
    }
    transaction(db, () => {
      db.prepare("INSERT OR REPLACE INTO asp_participants VALUES (?, ?, ?)").run(
        chatId,
        participant.handle,
        JSON.stringify(participant),
      );
    });
  }

  #ensureUniqueToken(token: string): void {
    if (this.agentByToken.has(token)) {
      throw new Error("duplicate agent token in seed");
    }
  }

  #rows(db: DatabaseSync, sql: string, ...params: Array<string | number>): SqlRow[] {
    const rows: SqlRow[] = [];
    for (const row of db.prepare(sql).all(...params)) {
      if (!isSqlRow(row)) {
        throw new Error("sqlite row is not an object");
      }
      rows.push(row);
    }
    return rows;
  }
}
