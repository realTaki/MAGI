/** ASP's worker on a BUS: directory lives in Books; relay gaps stay in asp.sqlite. */

import { eq } from "drizzle-orm";
import { BaseWorker, Bus, SYSTEM_CONTACT_ID } from "@magi/bus";

import { LocalDatabase } from "./db/database.js";
import { aspEvents } from "./db/tables/events.js";
import { aspMessageRecipients } from "./db/tables/messageRecipients.js";
import { relayAllowlist, relayPrincipals } from "./db/tables/relayPrincipals.js";
import { relayChats } from "./db/tables/relayChats.js";
import { relayParticipants } from "./db/tables/relayParticipants.js";
import { OPERATOR_HANDLE } from "./operator.js";
import type { Agent, Chat, ChatEvent, Participant } from "./store.js";

export const ASP_HANDLE = "@asp.magi";
export const ASP_CHANNEL = "asp";

export class RelayWorker extends BaseWorker {
  readonly worker_name = "relay";

  constructor(bus: Bus, private readonly storage: LocalDatabase) {
    super(bus);
  }

  async poll(): Promise<boolean> {
    return false;
  }

  loadDirectory(): {
    agents: Agent[];
    chats: Chat[];
    participants: Array<{ chatId: string; participant: Participant }>;
    chatSeq: Array<[string, number]>;
  } {
    const db = this.#db();
    const allowlists = new Map<string, Set<string>>();
    for (const row of db.select().from(relayAllowlist).all()) {
      const allowlist = allowlists.get(row.contact_handle) ?? new Set<string>();
      allowlist.add(row.allowed_handle);
      allowlists.set(row.contact_handle, allowlist);
    }
    const agents: Agent[] = [];
    for (const row of db.select().from(relayPrincipals).all()) {
      const contact = this.contactFor(row.handle);
      agents.push({
        handle: row.handle,
        token: row.token,
        name: row.handle === OPERATOR_HANDLE ? null : contact.name,
        nickname: contact.nickname,
        inbound_policy: row.inbound_policy,
        allowlist: allowlists.get(row.handle) ?? new Set<string>(),
        managed: row.managed,
      });
    }
    const chats: Chat[] = [];
    const chatSeq: Array<[string, number]> = [];
    for (const row of db.select().from(relayChats).all()) {
      const book = this.bus.chats.forChannel(ASP_CHANNEL, row.id);
      chats.push({
        id: row.id,
        creator: row.creator,
        state: row.state,
        topic: book.topic === "" ? null : book.topic,
        created_at: row.created_at,
        ended_at: row.ended_at,
        description: row.description,
        kind: row.kind,
      });
      chatSeq.push([row.id, row.next_sequence]);
    }
    const participants: Array<{ chatId: string; participant: Participant }> = [];
    for (const row of db.select().from(relayParticipants).all()) {
      participants.push({
        chatId: row.chat_id,
        participant: {
          handle: row.handle,
          status: row.status,
          joined_at: row.joined_at,
          left_at: row.left_at,
        },
      });
    }
    return { agents, chats, participants, chatSeq };
  }

  savePrincipal(agent: Agent): void {
    this.contactFor(agent.handle, agent.name);
    if (agent.nickname !== undefined) {
      const contact = this.contactFor(agent.handle);
      if (contact.nickname !== agent.nickname) {
        this.bus.contacts.update(contact.id, { nickname: agent.nickname });
      }
    }
    const db = this.#db();
    db.transaction((tx) => {
      tx.insert(relayPrincipals).values({
        handle: agent.handle,
        token: agent.token,
        inbound_policy: agent.inbound_policy,
        managed: agent.managed,
      }).onConflictDoUpdate({
        target: relayPrincipals.handle,
        set: {
          token: agent.token,
          inbound_policy: agent.inbound_policy,
          managed: agent.managed,
        },
      }).run();
      tx.delete(relayAllowlist).where(eq(relayAllowlist.contact_handle, agent.handle)).run();
      const allowlist = [...agent.allowlist].sort().map((allowed_handle) => ({
        contact_handle: agent.handle,
        allowed_handle,
      }));
      if (allowlist.length > 0) {
        tx.insert(relayAllowlist).values(allowlist).run();
      }
    }, { behavior: "immediate" });
  }

  saveChat(chat: Chat, nextSequence: number): void {
    const book = this.bus.chats.forChannel(ASP_CHANNEL, chat.id);
    this.bus.chats.setTopic(book.id, chat.topic ?? "");
    this.#db().insert(relayChats).values({
      id: chat.id,
      creator: chat.creator,
      state: chat.state,
      created_at: chat.created_at,
      ended_at: chat.ended_at,
      description: chat.description,
      kind: chat.kind,
      next_sequence: nextSequence,
    }).onConflictDoUpdate({
      target: relayChats.id,
      set: {
        creator: chat.creator,
        state: chat.state,
        created_at: chat.created_at,
        ended_at: chat.ended_at,
        description: chat.description,
        kind: chat.kind,
        next_sequence: nextSequence,
      },
    }).run();
  }

  saveParticipant(chatId: string, participant: Participant): void {
    const contact = this.contactFor(participant.handle);
    const book = this.bus.chats.forChannel(ASP_CHANNEL, chatId);
    if (participant.status === "joined") this.bus.chatMembers.add(book.id, contact.id);
    if (participant.status === "left") this.bus.chatMembers.remove(book.id, contact.id);
    this.#db().insert(relayParticipants).values({ chat_id: chatId, ...participant })
      .onConflictDoUpdate({
        target: [relayParticipants.chat_id, relayParticipants.handle],
        set: participant,
      })
      .run();
  }

  recordEvent(event: ChatEvent, recipientHandles: string[]): void {
    if (event.chat_id === null || event.sequence === null) {
      throw new Error("chat event is missing chat_id or sequence");
    }
    const chatId = event.chat_id;
    const sequence = event.sequence;
    this.#db().transaction((tx) => {
      tx.insert(aspEvents).values({
        chat_id: chatId,
        sequence,
        event_id: event.event_id,
        type: event.type,
        created_at: event.created_at,
        payload_json: event.payload,
      }).run();
      if (event.type === "chat.message" && recipientHandles.length > 0) {
        tx.insert(aspMessageRecipients).values(
          recipientHandles.map((handle) => ({ event_id: event.event_id, handle })),
        ).run();
      }
      tx.update(relayChats).set({ next_sequence: sequence + 1 }).where(eq(relayChats.id, chatId)).run();
    }, { behavior: "immediate" });
  }

  contactFor(handle: string, name?: string | null) {
    if (handle === OPERATOR_HANDLE) {
      const operator = this.bus.contacts.get(SYSTEM_CONTACT_ID);
      if (!operator) throw new Error("system contact missing");
      return operator;
    }
    const contact = this.bus.contacts.forAspHandle(handle);
    if (name && name !== contact.name) {
      try {
        return this.bus.contacts.update(contact.id, { name });
      } catch {
        return contact;
      }
    }
    return contact;
  }

  #db() {
    const db = this.storage.db;
    if (db === null) throw new Error("LocalDatabase is not open");
    return db;
  }
}
