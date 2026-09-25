import { BaseWorker, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, type Bus, type Contact } from "../../bus/index.js";
import { AspClient, type AspEvent } from "./client.js";

export class AspWorker extends BaseWorker {
  readonly worker_name = "asp";
  private readonly client: AspClient;
  private nickname: string | null;

  constructor(bus: Bus, base: string, token: string) {
    super(bus);
    this.client = new AspClient(bus.handle, base, token);
    this.nickname = bus.contacts.get(MAGI_CONTACT_ID)?.nickname ?? null;
  }

  connect(): Promise<void> { return this.client.connect((event) => this.handle(event), (error) => this.report(error)); }
  close(): void { this.client.close(); }

  /** The manager starts and stops this worker like any other. */
  start(): void { void this.connect(); }
  stop(): void { this.close(); }

  /** Connecting happens in the background and retries on its own, so this is the state. */
  health(): string | null { return this.client.connected ? null : "ASP is not connected"; }

  async poll(): Promise<boolean> {
    const board = this.bus.board("DeliveryNotify");
    const job = board.claim(this.worker_name, (input) => input.channel === "asp");
    if (!job) return false;
    try {
      if (!job.input.address) throw new Error("delivery has no ASP session");
      await this.client.send(job.input.address, job.input.text);
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  /** The operator sent this event, so a failure handling it is theirs to hear about. */
  private async handle(event: AspEvent): Promise<Record<string, unknown> | void> {
    try {
      return await this.onEvent(event);
    } catch (error) {
      this.report(error, event.session_id);
    }
  }

  private report(error: unknown, session?: string): void {
    const text = `[asp] ${error instanceof Error ? error.message : String(error)}`;
    this.bus.publishNotice(text, session ? this.bus.conversations.forChannel("asp", session).id : undefined);
  }

  private async onEvent(event: AspEvent): Promise<Record<string, unknown> | void> {
    if (event.type === "agent.nickname.read") return { type: "agent.nickname.current", nickname: this.nickname };
    if (event.type === "agent.nickname.update") {
      const updated = typeof event.nickname === "string" && !!event.nickname.trim();
      if (updated) {
        this.nickname = event.nickname!.trim();
        this.bus.contacts.update(MAGI_CONTACT_ID, { nickname: this.nickname });
      }
      return { type: "agent.nickname.updated", request_id: event.request_id, ok: updated };
    }
    if (event.type === "agent.provider.update") {
      const board = this.bus.board("ChangeProviderNotify");
      const jobId = board.publish({
        provider: text(event.provider), api_key: text(event.api_key), model: text(event.model), base_url: text(event.base_url),
      }, this.worker_name);
      const deadline = Date.now() + 30_000;
      while (Date.now() < deadline) {
        const result = board.result(jobId);
        if (result) return { type: "agent.provider.updated", request_id: event.request_id, ok: result.status === "completed" };
        await Bun.sleep(10);
      }
      return { type: "agent.provider.updated", request_id: event.request_id, ok: false };
    }
    const id = event.session_id;
    if (!id) return;
    const payload = event.payload ?? {};
    if (event.type === "session.invited" && payload.invitee === this.bus.handle) {
      await this.client.join(id);
      // This MAGI is in the conversation too, so it belongs to its members.
      this.bus.conversationMembers.add(this.bus.conversations.forChannel("asp", id).id, MAGI_CONTACT_ID);
      const initial = payload.initial_message;
      if (typeof initial === "object" && initial !== null) {
        const message = initial as Record<string, unknown>;
        if (this.asked(message)) this.ingest(id, message);
        else this.record(id, message);
      }
    } else if (event.type === "session.message" && payload.sender !== this.bus.handle) {
      // Everything said in the session is recorded — that is the conversation's history —
      // but only what is asked of this MAGI opens a turn.
      if (this.asked(payload)) this.ingest(id, payload);
      else this.record(id, payload);
    }
    if (event.event_id) return { type: "session.ack", session_id: id, event_id: event.event_id };
  }

  /**
   * Whether the message is this MAGI's to answer.
   *
   * ASP puts the handles a message names in `mentions`. Naming someone is how a message
   * asks for them, so only the named MAGIs answer — a room full of them answering each
   * other is the loop this avoids. A message that names nobody is for whoever can help,
   * and the prompt tells the model that it may decide the answer is not its own.
   */
  private asked(payload: Record<string, unknown>): boolean {
    const mentions = payload.mentions;
    if (!Array.isArray(mentions) || mentions.length === 0) return true;
    return mentions.includes(this.bus.handle);
  }

  /** The text of a message, whatever shape the channel sent the content in. */
  private content(payload: Record<string, unknown>): string {
    const content = payload.content;
    if (typeof content === "string") return content.trim();
    if (!Array.isArray(content)) return "";
    return content.map((part) => (typeof part === "object" && part !== null && "text" in part && typeof part.text === "string" ? part.text : "")).join("").trim();
  }

  /**
   * Who spoke: a contact of their own, and a member of this conversation from now on.
   * The address is the conversation, the people on it are its members.
   */
  private remember(sessionId: string, sender: unknown): Contact | null {
    const handle = typeof sender === "string" ? sender.trim() : "";
    if (!handle) return null;
    const contact = handle === "user" ? this.bus.contacts.get(SYSTEM_CONTACT_ID) : this.bus.contacts.forAspHandle(handle);
    if (!contact) return null;
    this.bus.conversationMembers.add(this.bus.conversations.forChannel("asp", sessionId).id, contact.id);
    return contact;
  }

  /** Answer it: the message enters the conversation and opens a turn. */
  private ingest(sessionId: string, payload: Record<string, unknown>): void {
    const text = this.content(payload);
    if (!text) return;
    const contact = this.remember(sessionId, payload.sender);
    const conversation = this.bus.conversations.forChannel("asp", sessionId);
    // Home is where a notice with no conversation of its own goes. The operator's first
    // message establishes it, and only a tool moves it afterwards.
    if (contact?.id === SYSTEM_CONTACT_ID && this.bus.homeConversation() === null) {
      this.bus.setHomeConversation(conversation.id);
    }
    this.bus.publishChat({
      conversation_id: conversation.id,
      contact_id: contact?.id ?? SYSTEM_CONTACT_ID,
      text,
    }, this.worker_name);
  }

  /** Only record it: it was addressed to someone else, but the history keeps it. */
  private record(sessionId: string, payload: Record<string, unknown>): void {
    const text = this.content(payload);
    if (!text) return;
    const contact = this.remember(sessionId, payload.sender);
    this.bus.messages.add(this.bus.conversations.forChannel("asp", sessionId).id, contact?.id ?? SYSTEM_CONTACT_ID, text);
  }
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
