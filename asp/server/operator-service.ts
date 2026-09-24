/** Desktop operator commands and conversation views over the session service. */

import { randomBytes } from "node:crypto";

import { NotAllowed, NotFound, SessionService } from "./service.ts";
import { Store } from "./store.ts";
import type { Transport } from "./transport.ts";

export class OperatorService {
  readonly sessions: SessionService;
  readonly store: Store;
  readonly transport: Transport;

  constructor(sessions: SessionService, store: Store, transport: Transport) {
    this.sessions = sessions;
    this.store = store;
    this.transport = transport;
  }

  async createConversation(creator: string, kind: string): Promise<Record<string, unknown>> {
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
    const result = await this.sessions.createSession({
      creator,
      invite,
      topic: null,
      initialMessage: null,
      endAfterSend: false,
    });
    const session = this.store.getSession(result.sessionId);
    if (session !== undefined) {
      session.kind = kind;
      this.store.updateSession(session);
    }
    const view = this.conversationView(creator, result.sessionId);
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

  conversationView(caller: string, sessionId: string): Record<string, unknown> {
    const view = this.sessions.getSessionView(caller, sessionId);
    const session = this.store.getSession(sessionId);
    const agents = this.store
      .participantsIn(sessionId)
      .filter(
        (participant) =>
          participant.handle !== caller &&
          (participant.status === "invited" || participant.status === "joined"),
      )
      .map((participant) => participant.handle);
    view.conversation_id = sessionId;
    view.agents = agents;
    if (session !== undefined) {
      view.kind = session.kind ?? (agents.length === 1 ? "bot" : "group");
      if (session.description !== null) {
        view.description = session.description;
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

  listConversations(caller: string): Record<string, unknown>[] {
    const rows: Record<string, unknown>[] = [];
    for (const session of this.store.sessions.values()) {
      if (this.store.getParticipant(session.id, caller) === undefined) {
        continue;
      }
      rows.push(this.conversationView(caller, session.id));
    }
    rows.sort((left, right) => Number(right.created_at ?? 0) - Number(left.created_at ?? 0));
    return rows;
  }

  listBots(caller: string, conversationId: string | null = null): Record<string, unknown>[] {
    let inConversation: Set<string> | null = null;
    if (conversationId !== null) {
      if (
        this.store.getSession(conversationId) === undefined ||
        this.store.getParticipant(conversationId, caller) === undefined
      ) {
        throw new NotFound();
      }
      inConversation = new Set(
        this.store
          .participantsIn(conversationId)
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
      if (inConversation !== null) {
        row.in_conversation = inConversation.has(handle);
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

  async addConversationMember(
    caller: string,
    sessionId: string,
    handle: string,
  ): Promise<Record<string, unknown>> {
    if (handle === caller) {
      throw new NotAllowed();
    }
    if (this.store.getAgent(handle) === undefined) {
      throw new NotFound();
    }
    await this.sessions.invite(caller, sessionId, [handle]);
    return this.conversationView(caller, sessionId);
  }

  async updateConversation(
    caller: string,
    sessionId: string,
    topic: string | null,
    description: string | null,
  ): Promise<Record<string, unknown>> {
    await this.sessions.updateSession(caller, sessionId, topic, description);
    return this.conversationView(caller, sessionId);
  }
}
