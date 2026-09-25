/** Session lifecycle and event fan-out. */

import type { Participant, Session, SessionEvent, Store } from "./store.ts";
import { eventToWire, makeId, nowMs } from "./store.ts";
import type { Transport } from "./transport.ts";

export class NotFound extends Error {}
export class Conflict extends Error {}
export class NotAllowed extends Error {}

export type CreateSessionResult = {
  sessionId: string;
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

export class SessionService {
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

  createSession(input: {
    creator: string;
    invite: string[];
    topic: string | null;
    initialMessage: MessageInput | null;
    endAfterSend: boolean;
    idempotencyKey?: string | null;
  }): Promise<CreateSessionResult> {
    return this.#lock.run(async () => {
      const idempotencyKey = input.idempotencyKey ?? null;
      if (idempotencyKey !== null) {
        const cached = this.store.getIdempotentSession(input.creator, idempotencyKey);
        if (cached !== undefined) {
          return { sessionId: cached[0], sequence: cached[1] };
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

      const session = this.store.createSession({ creator: input.creator, topic: input.topic });
      this.store.addParticipant(session.id, input.creator, "joined");
      for (const handle of invitees) {
        this.store.addParticipant(session.id, handle, "invited");
      }

      let initialSequence: number | null = null;
      const initialMessageId = input.initialMessage === null ? null : makeId("msg");
      for (const handle of invitees) {
        const payload: Record<string, unknown> = { invitee: handle, by: input.creator };
        if (input.topic !== null) {
          payload.topic = input.topic;
        }
        Object.assign(payload, this.#intranetInviteFields());
        this.store.appendSessionEvent(session.id, "session.invited", payload);
      }

      let messagePayload: Record<string, unknown> | null = null;
      if (input.initialMessage !== null) {
        messagePayload = {
          id: initialMessageId,
          session_id: session.id,
          sender: input.creator,
          sequence: this.store.sessionSeq.get(session.id),
          content: input.initialMessage.content,
          created_at: nowMs(),
        };
        if ("metadata" in input.initialMessage) {
          messagePayload.metadata = input.initialMessage.metadata;
        }
        const mentions = this.#mentions(session.id, input.initialMessage.content);
        if (mentions.length > 0) {
          messagePayload.mentions = mentions;
        }
        const event = this.store.appendSessionEvent(session.id, "session.message", messagePayload);
        messagePayload.sequence = event.sequence;
        initialSequence = event.sequence;
        if (input.endAfterSend) {
          for (const invited of this.store.eventsForSession(session.id)) {
            if (invited.type === "session.invited") {
              invited.payload.initial_message = messagePayload;
              this.store.updateEvent(invited);
            }
          }
        }
      }

      if (input.endAfterSend) {
        this.store.endSession(session.id);
        this.store.appendSessionEvent(session.id, "session.ended", { ended_by: input.creator });
      }
      await this.#fanOut(session.id);
      const result = { sessionId: session.id, sequence: initialSequence };
      if (idempotencyKey !== null) {
        this.store.recordIdempotentSession(
          input.creator,
          idempotencyKey,
          result.sessionId,
          result.sequence,
        );
      }
      return result;
    });
  }

  updateSession(
    caller: string,
    sessionId: string,
    topic: string | null,
    description: string | null,
  ): Promise<void> {
    return this.#lock.run(async () => {
      const session = this.#requireActiveSession(sessionId);
      this.#requireJoined(sessionId, caller);
      const payload: Record<string, unknown> = { by: caller };
      if (topic !== null) {
        session.topic = topic;
        payload.topic = topic;
      }
      if (description !== null) {
        session.description = description;
        payload.description = description;
      }
      this.store.updateSession(session);
      this.store.appendSessionEvent(sessionId, "session.updated", payload);
      await this.#fanOut(sessionId);
    });
  }

  join(handle: string, sessionId: string): Promise<void> {
    return this.#lock.run(async () => {
      const session = this.store.getSession(sessionId);
      if (session === undefined) {
        throw new NotFound();
      }
      if (session.state !== "active") {
        throw new Conflict("session is ended");
      }
      const participant = this.store.getParticipant(sessionId, handle);
      if (participant === undefined) {
        throw new NotFound();
      }
      if (participant.status === "joined") {
        return;
      }
      if (participant.status === "left") {
        throw new Conflict("cannot rejoin without re-invitation");
      }
      this.store.setStatus(sessionId, handle, "joined");
      this.store.appendSessionEvent(sessionId, "session.joined", { agent: handle });
      await this.#fanOut(sessionId);
    });
  }

  invite(caller: string, sessionId: string, invite: string[]): Promise<string[]> {
    return this.#lock.run(async () => {
      const session = this.#requireActiveSession(sessionId);
      this.#requireJoined(sessionId, caller);
      const invited: string[] = [];
      for (const handle of invite) {
        if (handle === caller) {
          continue;
        }
        const participant = this.store.getParticipant(sessionId, handle);
        if (
          participant !== undefined &&
          (participant.status === "invited" || participant.status === "joined")
        ) {
          continue;
        }
        if (participant !== undefined && participant.status === "left") {
          this.store.setStatus(sessionId, handle, "invited");
        } else {
          this.store.addParticipant(sessionId, handle, "invited");
        }
        const payload: Record<string, unknown> = { invitee: handle, by: caller };
        if (session.topic !== null) {
          payload.topic = session.topic;
        }
        Object.assign(payload, this.#intranetInviteFields());
        this.store.appendSessionEvent(sessionId, "session.invited", payload);
        invited.push(handle);
      }
      await this.#fanOut(sessionId);
      return invited;
    });
  }

  sendMessage(
    sender: string,
    sessionId: string,
    content: unknown,
    idempotencyKey: string | null,
    metadata: unknown,
  ): Promise<SendMessageResult> {
    return this.#lock.run(async () => {
      this.#requireActiveSession(sessionId);
      this.#requireJoined(sessionId, sender);
      if (idempotencyKey !== null) {
        const cached = this.store.getIdempotentMessage(sessionId, sender, idempotencyKey);
        if (cached !== undefined) {
          return { messageId: cached[0], sequence: cached[1] };
        }
      }
      const messageId = makeId("msg");
      const payload: Record<string, unknown> = {
        id: messageId,
        session_id: sessionId,
        sender,
        sequence: this.store.sessionSeq.get(sessionId),
        content,
        created_at: nowMs(),
      };
      if (idempotencyKey !== null) {
        payload.idempotency_key = idempotencyKey;
      }
      if (metadata !== null) {
        payload.metadata = metadata;
      }
      const mentions = this.#mentions(sessionId, content);
      if (mentions.length > 0) {
        payload.mentions = mentions;
      }
      const event = this.store.appendSessionEvent(sessionId, "session.message", payload);
      payload.sequence = event.sequence;
      if (idempotencyKey !== null) {
        if (event.sequence === null) {
          throw new Error("session message is missing a sequence");
        }
        this.store.recordIdempotentMessage(sessionId, sender, idempotencyKey, messageId, event.sequence);
      }
      await this.#fanOut(sessionId);
      if (event.sequence === null) {
        throw new Error("session message is missing a sequence");
      }
      return { messageId, sequence: event.sequence };
    });
  }

  leave(handle: string, sessionId: string): Promise<void> {
    return this.#lock.run(async () => {
      this.#requireActiveSession(sessionId);
      this.#requireJoined(sessionId, handle);
      this.store.setStatus(sessionId, handle, "left");
      this.store.appendSessionEvent(sessionId, "session.left", { agent: handle, reason: "left" });
      await this.#fanOut(sessionId);
    });
  }

  end(handle: string, sessionId: string): Promise<void> {
    return this.#lock.run(async () => {
      this.#requireActiveSession(sessionId);
      this.#requireJoined(sessionId, handle);
      this.store.endSession(sessionId);
      this.store.appendSessionEvent(sessionId, "session.ended", { ended_by: handle });
      await this.#fanOut(sessionId);
    });
  }

  reopen(
    handle: string,
    sessionId: string,
    invite: string[] | null,
    initialMessage: MessageInput | null,
  ): Promise<void> {
    return this.#lock.run(async () => {
      const session = this.store.getSession(sessionId);
      if (session === undefined) {
        throw new NotFound();
      }
      if (session.state !== "ended") {
        throw new Conflict("session is not ended");
      }
      const participant = this.store.getParticipant(sessionId, handle);
      if (participant === undefined || participant.status !== "joined") {
        throw new NotAllowed();
      }
      this.store.reopenSession(sessionId);
      this.store.appendSessionEvent(sessionId, "session.reopened", { reopened_by: handle });
      for (const invitee of invite ?? []) {
        if (invitee === handle) {
          continue;
        }
        const existing = this.store.getParticipant(sessionId, invitee);
        if (existing === undefined) {
          this.store.addParticipant(sessionId, invitee, "invited");
        } else {
          this.store.setStatus(sessionId, invitee, "invited");
        }
        this.store.appendSessionEvent(sessionId, "session.invited", {
          invitee,
          by: handle,
          ...this.#intranetInviteFields(),
        });
      }
      if (initialMessage !== null) {
        const payload: Record<string, unknown> = {
          id: makeId("msg"),
          session_id: sessionId,
          sender: handle,
          sequence: this.store.sessionSeq.get(sessionId),
          content: initialMessage.content,
          created_at: nowMs(),
        };
        const event = this.store.appendSessionEvent(sessionId, "session.message", payload);
        payload.sequence = event.sequence;
      }
      await this.#fanOut(sessionId);
    });
  }

  getSessionView(caller: string, sessionId: string): Record<string, unknown> {
    const session = this.store.getSession(sessionId);
    if (session === undefined || this.store.getParticipant(sessionId, caller) === undefined) {
      throw new NotFound();
    }
    const view: Record<string, unknown> = {
      id: session.id,
      state: session.state,
      participants: this.store.participantsIn(sessionId).map((participant) => {
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
      created_at: session.created_at,
    };
    if (session.topic !== null) {
      view.topic = session.topic;
    }
    if (session.description !== null) {
      view.description = session.description;
    }
    if (session.kind !== null) {
      view.kind = session.kind;
    }
    if (session.ended_at !== null) {
      view.ended_at = session.ended_at;
    }
    return view;
  }

  getEventsFor(
    caller: string,
    sessionId: string,
    afterSequence: number | null,
    limit: number | null,
  ): Record<string, unknown>[] {
    if (
      this.store.getSession(sessionId) === undefined ||
      this.store.getParticipant(sessionId, caller) === undefined
    ) {
      throw new NotFound();
    }
    let eligible = this.#filterEligibleHistory(
      caller,
      sessionId,
      this.store.eventsForSession(sessionId),
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

  acknowledge(caller: string, sessionId: string, eventIds: string[]): void {
    if (
      this.store.getSession(sessionId) === undefined ||
      this.store.getParticipant(sessionId, caller) === undefined
    ) {
      throw new NotFound();
    }
    this.store.acknowledge(caller, sessionId, eventIds);
  }

  #intranetInviteFields(): Record<string, unknown> {
    return { intranet: true };
  }

  #requireActiveSession(sessionId: string): Session {
    const session = this.store.getSession(sessionId);
    if (session === undefined) {
      throw new NotFound();
    }
    if (session.state !== "active") {
      throw new Conflict("session is ended");
    }
    return session;
  }

  #requireJoined(sessionId: string, handle: string): Participant {
    const participant = this.store.getParticipant(sessionId, handle);
    if (participant === undefined || participant.status !== "joined") {
      throw new NotAllowed();
    }
    return participant;
  }

  /**
   * Who a message is addressed to.
   *
   * A session holds several participants, so a message may name some of them —
   * `@eva-001.magi` or just `@eva-001`. A MAGI treats a message as its own work only when
   * it is named, which is what keeps a room full of them from answering each other
   * forever; a message that names nobody is for whoever wants to answer.
   */
  #mentions(sessionId: string, content: unknown): string[] {
    const text = typeof content === "string" ? content
      : Array.isArray(content) ? content.map((part) => (typeof part === "object" && part !== null && "text" in part && typeof part.text === "string" ? part.text : "")).join("")
      : "";
    if (!text.includes("@")) {
      return [];
    }
    const named = new Set([...text.matchAll(/@([A-Za-z0-9_.-]{1,64})/g)].map((match) => match[1]));
    const mentioned: string[] = [];
    for (const participant of this.store.participantsIn(sessionId)) {
      const handle = participant.handle.replace(/^@/, "");
      if (named.has(handle) || named.has(handle.replace(/\.magi$/, ""))) {
        mentioned.push(participant.handle);
      }
    }
    return mentioned;
  }

  async #fanOut(sessionId: string): Promise<void> {
    const events = this.store.eventsForSession(sessionId);
    for (const participant of this.store.participantsIn(sessionId)) {
      const cursor = this.transport.cursor(participant.handle, sessionId);
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

  #filterEligibleHistory(handle: string, sessionId: string, events: SessionEvent[]): SessionEvent[] {
    const session = this.store.getSession(sessionId);
    let status = session !== undefined && session.creator === handle ? "joined" : "absent";
    const out: SessionEvent[] = [];
    for (const event of events) {
      const payload = event.payload;
      const payloadAgent = payload.agent;
      const payloadInvitee = payload.invitee;
      let eligible = false;
      if (status === "joined") {
        eligible = true;
      } else if (status === "invited") {
        eligible = event.type === "session.invited" || event.type === "session.ended";
      } else if (status === "absent") {
        eligible = event.type === "session.invited" && payloadInvitee === handle;
      }
      if (eligible) {
        out.push(event);
      }
      if (event.type === "session.invited" && payloadInvitee === handle) {
        if (status === "absent" || status === "left") {
          status = "invited";
        }
      } else if (event.type === "session.joined" && payloadAgent === handle) {
        status = "joined";
      } else if (event.type === "session.left" && payloadAgent === handle) {
        status = "left";
      }
    }
    return out;
  }

  async #onWentOffline(handle: string): Promise<void> {
    for (const [key, participant] of [...this.store.participants]) {
      const sessionId = key.split("\0")[0];
      if (participant.handle !== handle || participant.status !== "joined" || sessionId === undefined) {
        continue;
      }
      const session = this.store.getSession(sessionId);
      if (session === undefined || session.state !== "active") {
        continue;
      }
      this.store.appendSessionEvent(sessionId, "session.disconnected", { agent: handle });
      await this.#fanOut(sessionId);
    }
  }

  async #onBackOnline(handle: string): Promise<void> {
    for (const [key, participant] of [...this.store.participants]) {
      const sessionId = key.split("\0")[0];
      if (participant.handle !== handle || participant.status !== "joined" || sessionId === undefined) {
        continue;
      }
      const session = this.store.getSession(sessionId);
      if (session === undefined || session.state !== "active") {
        continue;
      }
      this.store.appendSessionEvent(sessionId, "session.reconnected", { agent: handle });
    }
    for (const [key, participant] of [...this.store.participants]) {
      const sessionId = key.split("\0")[0];
      if (participant.handle !== handle || sessionId === undefined) {
        continue;
      }
      const cursor = this.transport.cursor(handle, sessionId);
      for (const event of this.store.eventsForSession(sessionId)) {
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
      const sessionId = key.split("\0")[0];
      if (participant.handle !== handle || participant.status !== "joined" || sessionId === undefined) {
        continue;
      }
      this.store.setStatus(sessionId, handle, "left");
      this.store.appendSessionEvent(sessionId, "session.left", {
        agent: handle,
        reason: "grace_expired",
      });
      affected.push(sessionId);
    }
    for (const sessionId of affected) {
      await this.#fanOut(sessionId);
    }
  }
}

function eligibleNow(participant: Participant, event: SessionEvent): boolean {
  if (participant.status === "joined") {
    return true;
  }
  if (participant.status === "invited") {
    return event.type === "session.invited" || event.type === "session.ended";
  }
  return false;
}

