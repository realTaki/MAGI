import { BaseWorker, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, messageDelivery, waitForJob, type Bus, type Contact } from "@magi/bus";
import { AspClient, type AspEvent } from "./client.js";

/** What a MAGI needs to speak on ASP; Shell writes these after the relay is up. */
type Credentials = { base: string; token: string };

export class AspWorker extends BaseWorker {
  readonly worker_name = "asp";
  private client: AspClient | null = null;
  private nickname: string | null;

  /** `override` is for running a MAGI by hand; normally the settings have the token. */
  constructor(bus: Bus, private readonly override?: Credentials) {
    super(bus);
    this.nickname = bus.contacts.get(MAGI_CONTACT_ID)?.nickname ?? null;
  }

  /** No relay address, nothing to do — the supervisor asks this before starting. */
  configured(): boolean { return this.credentials() !== null; }

  connect(): Promise<void> {
    const credentials = this.credentials();
    if (!credentials) return Promise.resolve();
    this.client ??= new AspClient(this.bus.handle, credentials.base, credentials.token);
    return this.client.connect((event) => this.handle(event), (error) => this.report(error));
  }
  close(): void {
    this.client?.close();
    this.client = null;
  }

  /** Connecting happens in the background and retries on its own. */
  start(): void { void this.connect().then(() => this.registerKey()); }
  stop(): void { this.close(); }

  /** Connecting happens in the background and retries on its own, so this is the state. */
  health(): string | null { return this.client?.connected ? null : "ASP is not connected"; }

  async poll(): Promise<boolean> {
    // Leave the message pending until the socket is up. Failing it here drops a
    // sync that was sent during connect.
    if (!this.client?.connected) return false;
    const board = this.bus.board("MessageDeliveryJob");
    // Its own channel, and only what this MAGI said: the chat knows the channel it is on
    // and the address it is delivered to, so the job carries neither.
    const job = board.claim(this.worker_name, (input) => {
      const message = this.bus.messages.get(input.message_id);
      return message?.contact_id === MAGI_CONTACT_ID && this.bus.chats.get(message.chat_id)?.channel === this.worker_name;
    });
    if (!job) return false;
    try {
      const message = this.bus.messages.get(job.input.message_id);
      if (!message) throw new Error(`message ${job.input.message_id} does not exist`);
      const chat = this.bus.chats.get(message.chat_id);
      if (!chat?.delivery_address) throw new Error("delivery has no ASP chat");
      const client = this.client;
      if (!client) throw new Error("ASP is not connected");
      const metadata = this.bus.outbox.take(message.id);
      await client.send(chat.delivery_address, message.content, metadata ?? undefined);
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
      this.report(error, event.chat_id);
    }
  }

  private report(error: unknown, chat?: string): void {
    const text = `[asp] ${error instanceof Error ? error.message : String(error)}`;
    messageDelivery.notify(this.bus, text, chat ? this.bus.chats.forChannel("asp", chat).id : undefined);
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
    const id = event.chat_id;
    if (!id) return;
    const payload = event.payload ?? {};
    // ASP replays what it has not seen acknowledged, so a sequence this workspace has
    // passed is a replay of something already handled and is skipped.
    if (this.taken(id, event.sequence)) {
      return event.event_id ? { type: "chat.ack", chat_id: id, event_id: event.event_id } : undefined;
    }
    if (event.type === "chat.invited" && payload.invitee === this.bus.handle) {
      if (!this.client) return;
      await this.client.join(id);
      // This MAGI is in the chat too, so it belongs to its members.
      this.bus.chatMembers.add(this.bus.chats.forChannel("asp", id).id, MAGI_CONTACT_ID);
      const initial = payload.initial_message;
      if (typeof initial === "object" && initial !== null) {
        const message = initial as Record<string, unknown>;
        // The same message also arrives as an event of its own: whichever comes first wins.
        if (!this.taken(id, message.sequence)) {
          if (this.asked(message)) this.ingest(id, message);
          else this.record(id, message);
        }
      }
    } else if (event.type === "chat.message" && payload.sender !== this.bus.handle) {
      if (await this.structured(id, payload)) {
        // A card result or a key exchange is done. It is not a turn.
      } else if (this.asked(payload)) this.ingest(id, payload);
      else this.record(id, payload);
    }
    if (event.event_id) return { type: "chat.ack", chat_id: id, event_id: event.event_id };
  }

  /**
   * Whether this sequence was already taken in, recording it when it was not.
   *
   * The reading is recorded *before* the event is handled on purpose: a failure here is
   * reported and the event is acknowledged anyway, so handling it twice is the worse
   * mistake of the two.
   */
  private taken(chatId: string, sequence: unknown): boolean {
    if (typeof sequence !== "number") return false;
    if (sequence <= this.bus.chats.read("asp", chatId)) return true;
    this.bus.chats.markRead("asp", chatId, sequence);
    return false;
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
   * Who spoke: a contact of their own, and a member of this chat from now on.
   * The address is the chat, the people on it are its members.
   */
  private remember(chatId: string, sender: unknown): Contact | null {
    const handle = typeof sender === "string" ? sender.trim() : "";
    if (!handle) return null;
    const contact = handle === "@user.magi" ? this.bus.contacts.get(SYSTEM_CONTACT_ID) : this.bus.contacts.forAspHandle(handle);
    if (!contact) return null;
    this.bus.chatMembers.add(this.bus.chats.forChannel("asp", chatId).id, contact.id);
    return contact;
  }

  /** Answer it: the message enters the chat and opens a turn. */
  private ingest(chatId: string, payload: Record<string, unknown>): void {
    const text = this.content(payload);
    if (!text) return;
    const contact = this.remember(chatId, payload.sender);
    const chat = this.bus.chats.forChannel("asp", chatId);
    // Home is where a notice with no chat of its own goes. The operator's first
    // message establishes it, and only a tool moves it afterwards.
    if (contact?.id === SYSTEM_CONTACT_ID && messageDelivery.homeChat(this.bus) === null) {
      messageDelivery.setHomeChat(this.bus, chat.id);
    }
    messageDelivery.send(this.bus, chat.id, text, contact?.id, this.worker_name);
  }

  /** Only record it: it was addressed to someone else, but the history keeps it. */
  private record(chatId: string, payload: Record<string, unknown>): void {
    const text = this.content(payload);
    if (!text) return;
    const contact = this.remember(chatId, payload.sender);
    messageDelivery.record(this.bus, this.bus.chats.forChannel("asp", chatId).id, text, contact?.id);
  }

  private async registerKey(): Promise<void> {
    const client = this.client;
    if (!client?.connected) return;
    try {
      await client.registerKey(this.bus.identity.publicKey);
    } catch (error) {
      this.report(error);
    }
  }

  /**
   * A structured field on a message: a card result, a key exchange, or a provider
   * sync. The ciphertext never becomes message text. Returns true when the
   * message was fully handled.
   */
  private async structured(chatId: string, payload: Record<string, unknown>): Promise<boolean> {
    const metadata = payload.metadata;
    if (!isRecord(metadata)) return false;
    if (metadata.card_result !== undefined) {
      await this.completeCard(metadata.card_result);
      this.record(chatId, { sender: payload.sender, content: this.content(payload) || "card filled" });
      return true;
    }
    const envelope = metadata.envelope;
    if (!isRecord(envelope) || typeof envelope.kind !== "string") return false;
    if (envelope.kind === "identity.key_request") {
      await this.registerKey();
      const chat = this.bus.chats.forChannel("asp", chatId);
      messageDelivery.send(this.bus, chat.id, "provider sync: public key ready", MAGI_CONTACT_ID, this.worker_name, {
        envelope: { kind: "identity.key_ready", public_key: this.bus.identity.publicKey },
      });
      this.record(chatId, { sender: payload.sender, content: "provider sync: public key requested" });
      return true;
    }
    if (envelope.kind === "identity.key_ready" && typeof envelope.public_key === "string") {
      this.completePeerKey(payload.sender, envelope.public_key);
      this.record(chatId, { sender: payload.sender, content: "provider sync: public key ready" });
      return true;
    }
    if (envelope.kind === "provider.sync") {
      await this.applySync(envelope);
      const summary = this.content(payload) || syncSummary(envelope);
      this.ingest(chatId, { sender: payload.sender, content: summary });
      return true;
    }
    return false;
  }

  private async applySync(envelope: Record<string, unknown>): Promise<void> {
    if (typeof envelope.ciphertext !== "string" || envelope.ciphertext === "") {
      throw new Error("provider sync is missing a ciphertext");
    }
    const settings: Record<string, string> = {};
    if (typeof envelope.provider === "string" && envelope.provider !== "") settings["provider.name"] = envelope.provider;
    if (typeof envelope.model === "string" && envelope.model !== "") settings["provider.model"] = envelope.model;
    if (typeof envelope.base_url === "string" && envelope.base_url !== "") settings["provider.base_url"] = envelope.base_url;
    const id = this.bus.board("ConfigureJob").publish({
      settings,
      sealed: { "provider.api_key": envelope.ciphertext },
      reload: ["providers"],
    }, this.worker_name);
    const result = await waitForJob(() => this.bus.board("ConfigureJob").result(id), 30_000, "ConfigureJob");
    if (result.status === "failed") throw new Error(result.error ?? "provider sync failed");
  }

  private async completeCard(result: unknown): Promise<void> {
    if (!isRecord(result) || typeof result.id !== "string") throw new Error("card result is missing an id");
    const board = this.bus.board("CardJob");
    const job = board.claim(this.worker_name, (input) => input.spec.id === result.id);
    if (!job) throw new Error(`no open card ${result.id}`);
    try {
      const fields = isRecord(result.fields) ? result.fields : {};
      const sealed: Record<string, string> = {};
      const visible: Record<string, string> = {};
      for (const field of job.input.spec.fields) {
        const value = fields[field.name];
        if (field.kind === "secret") {
          if (!isRecord(value) || typeof value.ciphertext !== "string" || value.ciphertext === "") {
            throw new Error(`card field ${field.name} is missing a ciphertext`);
          }
          sealed[field.name] = value.ciphertext;
          visible[field.name] = "updated";
        } else if (typeof value === "string") {
          visible[field.name] = value;
        } else {
          throw new Error(`card field ${field.name} is missing a value`);
        }
      }
      if (Object.keys(sealed).length > 0) {
        const reload = Object.keys(sealed).some((key) => key === "provider.api_key" || key.startsWith("provider.")) ? ["providers"] : [];
        const id = this.bus.board("ConfigureJob").publish({ sealed, reload }, this.worker_name);
        const stored = await waitForJob(() => this.bus.board("ConfigureJob").result(id), 30_000, "ConfigureJob");
        if (stored.status === "failed") throw new Error(stored.error ?? "could not store card secrets");
      }
      board.submit(this.worker_name, job.id, { output: { fields: visible } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
      throw error;
    }
  }

  private completePeerKey(sender: unknown, publicKey: string): void {
    const handle = typeof sender === "string" ? sender : "";
    const board = this.bus.board("PeerKeyJob");
    const job = board.claim(this.worker_name, (input) => input.handle === handle);
    if (!job) return;
    board.submit(this.worker_name, job.id, { output: { public_key: publicKey } });
  }

  /** Read at every start, so a token that arrives later is picked up by a restart. */
  private credentials(): Credentials | null {
    const base = this.override?.base ?? this.bus.settings.get("asp.base")?.trim();
    const token = this.override?.token ?? this.bus.settings.get("asp.token")?.trim();
    if (!base || !token) return null;
    return { base, token };
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function syncSummary(envelope: Record<string, unknown>): string {
  const provider = typeof envelope.provider === "string" ? envelope.provider : "provider";
  const model = typeof envelope.model === "string" ? envelope.model : "model";
  return `provider sync: ${provider}, ${model}, key updated`;
}
