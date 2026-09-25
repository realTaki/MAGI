/** Desktop operator commands and chat views over the chat service. */

import { randomBytes } from "node:crypto";

import { NotAllowed, NotFound, ChatService } from "./service.ts";
import { Store } from "./store.ts";
import type { Transport } from "./transport.ts";

export class OperatorService {
  readonly chats: ChatService;
  readonly store: Store;
  readonly transport: Transport;

  constructor(chats: ChatService, store: Store, transport: Transport) {
    this.chats = chats;
    this.store = store;
    this.transport = transport;
  }

  async createChat(creator: string, kind: string): Promise<Record<string, unknown>> {
    if (kind !== "bot" && kind !== "group") {
      throw new Error("kind must be bot or group");
    }
    let invite: string[] = [];
    let magi: { handle: string; name: string; token: string } | null = null;
    if (kind === "bot") {
      const magiName = this.store.nextMagiName();
      const handle = Store.magiHandle(magiName);
      const magiToken = randomBytes(24).toString("base64url");
      this.store.registerAgent({ handle, token: magiToken, name: magiName, managed: true });
      magi = { handle, name: magiName, token: magiToken };
      invite = [handle];
    }
    const result = await this.chats.createChat({
      creator,
      invite,
      topic: null,
      initialMessage: null,
      endAfterSend: false,
    });
    const chat = this.store.getChat(result.chatId);
    if (chat !== undefined) {
      chat.kind = kind;
      this.store.updateChat(chat);
    }
    const view = this.chatView(creator, result.chatId);
    if (magi !== null) {
      view.name = magi.name;
      // ASP registers the agent but never starts it: the caller (the desktop
      // app on this machine) runs the process itself, and this is where it
      // gets the handle and token to start it with.
      view.magi = { handle: magi.handle, name: magi.name, token: magi.token };
    }
    return view;
  }

  /**
   * Every registered agent with the credential its runner needs. Operator only:
   * a MAGI token authenticates a live socket, not the operator API.
   */
  listAgents(): Record<string, unknown>[] {
    const agents: Record<string, unknown>[] = [];
    for (const [handle, agent] of this.store.agents) {
      agents.push({
        handle,
        token: agent.token,
        name: agent.name,
        nickname: agent.nickname,
        managed: agent.managed,
        online: this.transport.isOnline(handle),
      });
    }
    return agents;
  }

  chatView(caller: string, chatId: string): Record<string, unknown> {
    const view = this.chats.getChatView(caller, chatId);
    const chat = this.store.getChat(chatId);
    const agents = this.store
      .participantsIn(chatId)
      .filter(
        (participant) =>
          participant.handle !== caller &&
          (participant.status === "invited" || participant.status === "joined"),
      )
      .map((participant) => participant.handle);
    view.chat_id = chatId;
    view.agents = agents;
    if (chat !== undefined) {
      view.kind = chat.kind ?? (agents.length === 1 ? "bot" : "group");
      if (chat.description !== null) {
        view.description = chat.description;
      }
    }
    if (view.kind === "bot" && agents.length === 1) {
      const agent = this.store.getAgent(agents[0] ?? "");
      const handle = agents[0];
      if (agent !== undefined && handle !== undefined) {
        view.name = agent.nickname ?? agent.name ?? handle;
      }
    }
    return view;
  }

  listChats(caller: string): Record<string, unknown>[] {
    const rows: Record<string, unknown>[] = [];
    for (const chat of this.store.chats.values()) {
      if (this.store.getParticipant(chat.id, caller) === undefined) {
        continue;
      }
      rows.push(this.chatView(caller, chat.id));
    }
    rows.sort((left, right) => Number(right.created_at ?? 0) - Number(left.created_at ?? 0));
    return rows;
  }

  listBots(caller: string, chatId: string | null = null): Record<string, unknown>[] {
    let inChat: Set<string> | null = null;
    if (chatId !== null) {
      if (
        this.store.getChat(chatId) === undefined ||
        this.store.getParticipant(chatId, caller) === undefined
      ) {
        throw new NotFound();
      }
      inChat = new Set(
        this.store
          .participantsIn(chatId)
          .filter(
            (participant) => participant.status === "invited" || participant.status === "joined",
          )
          .map((participant) => participant.handle),
      );
    }
    const bots: Record<string, unknown>[] = [];
    for (const handle of this.store.agents.keys()) {
      if (handle === caller) {
        continue;
      }
      const agent = this.store.getAgent(handle);
      const name = agent === undefined ? null : agent.nickname ?? agent.name;
      const row: Record<string, unknown> = {
        handle,
        name: name ?? handle,
        online: this.transport.isOnline(handle),
      };
      if (inChat !== null) {
        row.in_chat = inChat.has(handle);
      }
      bots.push(row);
    }
    bots.sort((left, right) => {
      const a = String(left.handle);
      const b = String(right.handle);
      return a < b ? -1 : a > b ? 1 : 0;
    });
    return bots;
  }

  async addChatMember(
    caller: string,
    chatId: string,
    handle: string,
  ): Promise<Record<string, unknown>> {
    if (handle === caller) {
      throw new NotAllowed();
    }
    if (this.store.getAgent(handle) === undefined) {
      throw new NotFound();
    }
    await this.chats.invite(caller, chatId, [handle]);
    return this.chatView(caller, chatId);
  }

  async updateChat(
    caller: string,
    chatId: string,
    topic: string | null,
    description: string | null,
  ): Promise<Record<string, unknown>> {
    await this.chats.updateChat(caller, chatId, topic, description);
    return this.chatView(caller, chatId);
  }
}
