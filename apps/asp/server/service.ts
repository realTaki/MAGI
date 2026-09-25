/** Chat lifecycle and event fan-out. */

import type { Participant, Chat, ChatEvent, Store } from "./store.ts";
import { eventToWire, makeId, nowMs } from "./store.ts";
import type { Transport } from "./transport.ts";

export class NotFound extends Error {}
export class Conflict extends Error {}
export class NotAllowed extends Error {}

export type CreateChatResult = {
  chatId: string;
  sequence: number | null;
};

export type SendMessageResult = {
  messageId: string;
  sequence: number;
};

type MessageInput = {
  content: unknown;
  metadata?: unknown;
};

export class Mutex {
  #chain: Promise<void> = Promise.resolve();

  run<T>(action: () => Promise<T> | T): Promise<T> {
    const result = this.#chain.then(action, action);
    this.#chain = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }
}

export class ChatService {
  readonly #lock = new Mutex();
  readonly store: Store;
  readonly transport: Transport;

  constructor(store: Store, transport: Transport) {
    this.store = store;
    this.transport = transport;
    transport.setLifecycleHooks({
      onWentOffline: (handle) => this.#onWentOffline(handle),
      onBackOnline: (handle) => this.#onBackOnline(handle),
      onGraceExpired: (handle) => this.#onGraceExpired(handle),
    });
  }

  createChat(input: {
    creator: string;
    invite: string[];
    topic: string | null;
    initialMessage: MessageInput | null;
    endAfterSend: boolean;
    idempotencyKey?: string | null;
  }): Promise<CreateChatResult> {
    return this.#lock.run(async () => {
      const idempotencyKey = input.idempotencyKey ?? null;
      if (idempotencyKey !== null) {
        const cached = this.store.getIdempotentChat(input.creator, idempotencyKey);
        if (cached !== undefined) {
          return { chatId: cached[0], sequence: cached[1] };
        }
      }

      const invitees: string[] = [];
      const seen = new Set([input.creator]);
      for (const handle of input.invite) {
        if (seen.has(handle)) {
          continue;
        }
        seen.add(handle);
        invitees.push(handle);
      }

      const chat = this.store.createChat({ creator: input.creator, topic: input.topic });
      this.store.addParticipant(chat.id, input.creator, "joined");
      for (const handle of invitees) {
        this.store.addParticipant(chat.id, handle, "invited");
      }

      let initialSequence: number | null = null;
      const initialMessageId = input.initialMessage === null ? null : makeId("msg");
      for (const handle of invitees) {
        const payload: Record<string, unknown> = { invitee: handle, by: input.creator };
        if (input.topic !== null) {
          payload.topic = input.topic;
        }
        Object.assign(payload, this.#intranetInviteFields());
        this.store.appendChatEvent(chat.id, "chat.invited", payload);
      }

      let messagePayload: Record<string, unknown> | null = null;
      if (input.initialMessage !== null) {
        messagePayload = {
          id: initialMessageId,
          chat_id: chat.id,
          sender: input.creator,
          sequence: this.store.chatSeq.get(chat.id),
          content: input.initialMessage.content,
          created_at: nowMs(),
        };
        if ("metadata" in input.initialMessage) {
          messagePayload.metadata = input.initialMessage.metadata;
        }
        const mentions = this.#mentions(chat.id, input.initialMessage.content);
        if (mentions.length > 0) {
          messagePayload.mentions = mentions;
        }
        const event = this.store.appendChatEvent(chat.id, "chat.message", messagePayload);
        messagePayload.sequence = event.sequence;
        initialSequence = event.sequence;
        if (input.endAfterSend) {
          for (const invited of this.store.eventsForChat(chat.id)) {
            if (invited.type === "chat.invited") {
              invited.payload.initial_message = messagePayload;
              this.store.updateEvent(invited);
            }
          }
        }
      }

      if (input.endAfterSend) {
        this.store.endChat(chat.id);
        this.store.appendChatEvent(chat.id, "chat.ended", { ended_by: input.creator });
      }
      await this.#fanOut(chat.id);
      const result = { chatId: chat.id, sequence: initialSequence };
      if (idempotencyKey !== null) {
        this.store.recordIdempotentChat(
          input.creator,
          idempotencyKey,
          result.chatId,
          result.sequence,
        );
      }
      return result;
    });
  }

  updateChat(
    caller: string,
    chatId: string,
    topic: string | null,
    description: string | null,
  ): Promise<void> {
    return this.#lock.run(async () => {
      const chat = this.#requireActiveChat(chatId);
      this.#requireJoined(chatId, caller);
      const payload: Record<string, unknown> = { by: caller };
      if (topic !== null) {
        chat.topic = topic;
        payload.topic = topic;
      }
      if (description !== null) {
        chat.description = description;
        payload.description = description;
      }
      this.store.updateChat(chat);
      this.store.appendChatEvent(chatId, "chat.updated", payload);
      await this.#fanOut(chatId);
    });
  }

  join(handle: string, chatId: string): Promise<void> {
    return this.#lock.run(async () => {
      const chat = this.store.getChat(chatId);
      if (chat === undefined) {
        throw new NotFound();
      }
      if (chat.state !== "active") {
        throw new Conflict("chat is ended");
      }
      const participant = this.store.getParticipant(chatId, handle);
      if (participant === undefined) {
        throw new NotFound();
      }
      if (participant.status === "joined") {
        return;
      }
      if (participant.status === "left") {
        throw new Conflict("cannot rejoin without re-invitation");
      }
      this.store.setStatus(chatId, handle, "joined");
      this.store.appendChatEvent(chatId, "chat.joined", { agent: handle });
      await this.#fanOut(chatId);
    });
  }

  invite(caller: string, chatId: string, invite: string[]): Promise<string[]> {
    return this.#lock.run(async () => {
      const chat = this.#requireActiveChat(chatId);
      this.#requireJoined(chatId, caller);
      const invited: string[] = [];
      for (const handle of invite) {
        if (handle === caller) {
          continue;
        }
        const participant = this.store.getParticipant(chatId, handle);
        if (
          participant !== undefined &&
          (participant.status === "invited" || participant.status === "joined")
        ) {
          continue;
        }
        if (participant !== undefined && participant.status === "left") {
          this.store.setStatus(chatId, handle, "invited");
        } else {
          this.store.addParticipant(chatId, handle, "invited");
        }
        const payload: Record<string, unknown> = { invitee: handle, by: caller };
        if (chat.topic !== null) {
          payload.topic = chat.topic;
        }
        Object.assign(payload, this.#intranetInviteFields());
        this.store.appendChatEvent(chatId, "chat.invited", payload);
        invited.push(handle);
      }
      await this.#fanOut(chatId);
      return invited;
    });
  }

  sendMessage(
    sender: string,
    chatId: string,
    content: unknown,
    idempotencyKey: string | null,
    metadata: unknown,
  ): Promise<SendMessageResult> {
    return this.#lock.run(async () => {
      this.#requireActiveChat(chatId);
      this.#requireJoined(chatId, sender);
      if (idempotencyKey !== null) {
        const cached = this.store.getIdempotentMessage(chatId, sender, idempotencyKey);
        if (cached !== undefined) {
          return { messageId: cached[0], sequence: cached[1] };
        }
      }
      const messageId = makeId("msg");
      const payload: Record<string, unknown> = {
        id: messageId,
        chat_id: chatId,
        sender,
        sequence: this.store.chatSeq.get(chatId),
        content,
        created_at: nowMs(),
      };
      if (idempotencyKey !== null) {
        payload.idempotency_key = idempotencyKey;
      }
      if (metadata !== null) {
        payload.metadata = metadata;
      }
      const mentions = this.#mentions(chatId, content);
      if (mentions.length > 0) {
        payload.mentions = mentions;
      }
      const event = this.store.appendChatEvent(chatId, "chat.message", payload);
      payload.sequence = event.sequence;
      if (idempotencyKey !== null) {
        if (event.sequence === null) {
          throw new Error("chat message is missing a sequence");
        }
        this.store.recordIdempotentMessage(chatId, sender, idempotencyKey, messageId, event.sequence);
      }
      await this.#fanOut(chatId);
      if (event.sequence === null) {
        throw new Error("chat message is missing a sequence");
      }
      return { messageId, sequence: event.sequence };
    });
  }

  leave(handle: string, chatId: string): Promise<void> {
    return this.#lock.run(async () => {
      this.#requireActiveChat(chatId);
      this.#requireJoined(chatId, handle);
      this.store.setStatus(chatId, handle, "left");
      this.store.appendChatEvent(chatId, "chat.left", { agent: handle, reason: "left" });
      await this.#fanOut(chatId);
    });
  }

  end(handle: string, chatId: string): Promise<void> {
    return this.#lock.run(async () => {
      this.#requireActiveChat(chatId);
      this.#requireJoined(chatId, handle);
      this.store.endChat(chatId);
      this.store.appendChatEvent(chatId, "chat.ended", { ended_by: handle });
      await this.#fanOut(chatId);
    });
  }

  reopen(
    handle: string,
    chatId: string,
    invite: string[] | null,
    initialMessage: MessageInput | null,
  ): Promise<void> {
    return this.#lock.run(async () => {
      const chat = this.store.getChat(chatId);
      if (chat === undefined) {
        throw new NotFound();
      }
      if (chat.state !== "ended") {
        throw new Conflict("chat is not ended");
      }
      const participant = this.store.getParticipant(chatId, handle);
      if (participant === undefined || participant.status !== "joined") {
        throw new NotAllowed();
      }
      this.store.reopenChat(chatId);
      this.store.appendChatEvent(chatId, "chat.reopened", { reopened_by: handle });
      for (const invitee of invite ?? []) {
        if (invitee === handle) {
          continue;
        }
        const existing = this.store.getParticipant(chatId, invitee);
        if (existing === undefined) {
          this.store.addParticipant(chatId, invitee, "invited");
        } else {
          this.store.setStatus(chatId, invitee, "invited");
        }
        this.store.appendChatEvent(chatId, "chat.invited", {
          invitee,
          by: handle,
          ...this.#intranetInviteFields(),
        });
      }
      if (initialMessage !== null) {
        const payload: Record<string, unknown> = {
          id: makeId("msg"),
          chat_id: chatId,
          sender: handle,
          sequence: this.store.chatSeq.get(chatId),
          content: initialMessage.content,
          created_at: nowMs(),
        };
        const event = this.store.appendChatEvent(chatId, "chat.message", payload);
        payload.sequence = event.sequence;
      }
      await this.#fanOut(chatId);
    });
  }

  getChatView(caller: string, chatId: string): Record<string, unknown> {
    const chat = this.store.getChat(chatId);
    if (chat === undefined || this.store.getParticipant(chatId, caller) === undefined) {
      throw new NotFound();
    }
    const view: Record<string, unknown> = {
      id: chat.id,
      state: chat.state,
      participants: this.store.participantsIn(chatId).map((participant) => {
        const row: Record<string, unknown> = {
          handle: participant.handle,
          status: participant.status,
        };
        if (participant.joined_at !== null) {
          row.joined_at = participant.joined_at;
        }
        if (participant.left_at !== null) {
          row.left_at = participant.left_at;
        }
        return row;
      }),
      created_at: chat.created_at,
    };
    if (chat.topic !== null) {
      view.topic = chat.topic;
    }
    if (chat.description !== null) {
      view.description = chat.description;
    }
    if (chat.kind !== null) {
      view.kind = chat.kind;
    }
    if (chat.ended_at !== null) {
      view.ended_at = chat.ended_at;
    }
    return view;
  }

  getEventsFor(
    caller: string,
    chatId: string,
    afterSequence: number | null,
    limit: number | null,
  ): Record<string, unknown>[] {
    if (
      this.store.getChat(chatId) === undefined ||
      this.store.getParticipant(chatId, caller) === undefined
    ) {
      throw new NotFound();
    }
    let eligible = this.#filterEligibleHistory(
      caller,
      chatId,
      this.store.eventsForChat(chatId),
    );
    if (afterSequence !== null) {
      eligible = eligible.filter(
        (event) => event.sequence !== null && event.sequence > afterSequence,
      );
    }
    if (limit !== null) {
      eligible = eligible.slice(0, limit);
    }
    return eligible.map((event) => eventToWire(event));
  }

  acknowledge(caller: string, chatId: string, eventIds: string[]): void {
    if (
      this.store.getChat(chatId) === undefined ||
      this.store.getParticipant(chatId, caller) === undefined
    ) {
      throw new NotFound();
    }
    this.store.acknowledge(caller, chatId, eventIds);
  }

  #intranetInviteFields(): Record<string, unknown> {
    return { intranet: true };
  }

  #requireActiveChat(chatId: string): Chat {
    const chat = this.store.getChat(chatId);
    if (chat === undefined) {
      throw new NotFound();
    }
    if (chat.state !== "active") {
      throw new Conflict("chat is ended");
    }
    return chat;
  }

  #requireJoined(chatId: string, handle: string): Participant {
    const participant = this.store.getParticipant(chatId, handle);
    if (participant === undefined || participant.status !== "joined") {
      throw new NotAllowed();
    }
    return participant;
  }

  /**
   * Who a message is addressed to.
   *
   * A chat holds several participants, so a message may name some of them —
   * `@eva-001.magi` or just `@eva-001`. A MAGI treats a message as its own work only when
   * it is named, which is what keeps a room full of them from answering each other
   * forever; a message that names nobody is for whoever wants to answer.
   */
  #mentions(chatId: string, content: unknown): string[] {
    const text = typeof content === "string" ? content
      : Array.isArray(content) ? content.map((part) => (typeof part === "object" && part !== null && "text" in part && typeof part.text === "string" ? part.text : "")).join("")
      : "";
    if (!text.includes("@")) {
      return [];
    }
    const named = new Set([...text.matchAll(/@([A-Za-z0-9_.-]{1,64})/g)].map((match) => match[1]));
    const mentioned: string[] = [];
    for (const participant of this.store.participantsIn(chatId)) {
      const handle = participant.handle.replace(/^@/, "");
      if (named.has(handle) || named.has(handle.replace(/\.magi$/, ""))) {
        mentioned.push(participant.handle);
      }
    }
    return mentioned;
  }

  async #fanOut(chatId: string): Promise<void> {
    const events = this.store.eventsForChat(chatId);
    for (const participant of this.store.participantsIn(chatId)) {
      const cursor = this.transport.cursor(participant.handle, chatId);
      for (const event of events) {
        if (event.sequence === null || event.sequence <= cursor) {
          continue;
        }
        if (this.store.isAcknowledged(participant.handle, event.event_id)) {
          continue;
        }
        if (eligibleNow(participant, event)) {
          await this.transport.deliver(participant.handle, event);
        }
      }
    }
  }

  #filterEligibleHistory(handle: string, chatId: string, events: ChatEvent[]): ChatEvent[] {
    const chat = this.store.getChat(chatId);
    let status = chat !== undefined && chat.creator === handle ? "joined" : "absent";
    const out: ChatEvent[] = [];
    for (const event of events) {
      const payload = event.payload;
      const payloadAgent = payload.agent;
      const payloadInvitee = payload.invitee;
      let eligible = false;
      if (status === "joined") {
        eligible = true;
      } else if (status === "invited") {
        eligible = event.type === "chat.invited" || event.type === "chat.ended";
      } else if (status === "absent") {
        eligible = event.type === "chat.invited" && payloadInvitee === handle;
      }
      if (eligible) {
        out.push(event);
      }
      if (event.type === "chat.invited" && payloadInvitee === handle) {
        if (status === "absent" || status === "left") {
          status = "invited";
        }
      } else if (event.type === "chat.joined" && payloadAgent === handle) {
        status = "joined";
      } else if (event.type === "chat.left" && payloadAgent === handle) {
        status = "left";
      }
    }
    return out;
  }

  async #onWentOffline(handle: string): Promise<void> {
    for (const [key, participant] of [...this.store.participants]) {
      const chatId = key.split("\0")[0];
      if (participant.handle !== handle || participant.status !== "joined" || chatId === undefined) {
        continue;
      }
      const chat = this.store.getChat(chatId);
      if (chat === undefined || chat.state !== "active") {
        continue;
      }
      this.store.appendChatEvent(chatId, "chat.disconnected", { agent: handle });
      await this.#fanOut(chatId);
    }
  }

  async #onBackOnline(handle: string): Promise<void> {
    for (const [key, participant] of [...this.store.participants]) {
      const chatId = key.split("\0")[0];
      if (participant.handle !== handle || participant.status !== "joined" || chatId === undefined) {
        continue;
      }
      const chat = this.store.getChat(chatId);
      if (chat === undefined || chat.state !== "active") {
        continue;
      }
      this.store.appendChatEvent(chatId, "chat.reconnected", { agent: handle });
    }
    for (const [key, participant] of [...this.store.participants]) {
      const chatId = key.split("\0")[0];
      if (participant.handle !== handle || chatId === undefined) {
        continue;
      }
      const cursor = this.transport.cursor(handle, chatId);
      for (const event of this.store.eventsForChat(chatId)) {
        if (event.sequence === null || event.sequence <= cursor) {
          continue;
        }
        if (this.store.isAcknowledged(handle, event.event_id)) {
          continue;
        }
        if (eligibleNow(participant, event)) {
          await this.transport.deliver(handle, event);
        }
      }
    }
  }

  async #onGraceExpired(handle: string): Promise<void> {
    const affected: string[] = [];
    for (const [key, participant] of [...this.store.participants]) {
      const chatId = key.split("\0")[0];
      if (participant.handle !== handle || participant.status !== "joined" || chatId === undefined) {
        continue;
      }
      this.store.setStatus(chatId, handle, "left");
      this.store.appendChatEvent(chatId, "chat.left", {
        agent: handle,
        reason: "grace_expired",
      });
      affected.push(chatId);
    }
    for (const chatId of affected) {
      await this.#fanOut(chatId);
    }
  }
}

function eligibleNow(participant: Participant, event: ChatEvent): boolean {
  if (participant.status === "joined") {
    return true;
  }
  if (participant.status === "invited") {
    return event.type === "chat.invited" || event.type === "chat.ended";
  }
  return false;
}

