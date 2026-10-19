import { chat as receiveChat, type ChatInput, encryptFor, BaseWorker, ASP_MAGI_HANDLE, EVA_MAGI_HANDLE, MAGI_CONTACT_ID, SHELL_MAGI_HANDLE, SYSTEM_CONTACT_ID, USER_HANDLE, messageDelivery, type AspCommandJob, type Bus, type Contact } from "@magi/bus";
import { AspClient, type AspEvent } from "./client.js";

/** What a MAGI needs to speak on ASP; Shell writes these after the relay is up. */
type Credentials = { base: string; token: string };

const KNOWN_MAGI = new Set([SHELL_MAGI_HANDLE, ASP_MAGI_HANDLE, EVA_MAGI_HANDLE]);

export class AspWorker extends BaseWorker {
  readonly worker_name = "asp";
  private client: AspClient | null = null;
  private profilePending = true;
  private profileTask: Promise<void> | null = null;
  private profileRetryAt = 0;
  private command: Promise<void> | null = null;
  private keyTask: Promise<void> | null = null;
  private keyReady = false;
  private keyError: string | null = null;
  private keyRetryAt = 0;
  private unspoken: string | null = null;

  /** `override` is for running a MAGI by hand; normally the settings have the token. */
  constructor(bus: Bus, private readonly override?: Credentials) {
    super(bus);
  }

  /** No relay address, nothing to do. A throwaway health check must not connect or register a key. */
  configured(): boolean {
    if (process.env.MAGI_HEALTH === "1") return false;
    return this.credentials() !== null;
  }

  connect(): Promise<void> {
    const credentials = this.credentials();
    if (!credentials) return Promise.resolve();
    const client = this.client ??= new AspClient(this.bus.handle, credentials.base, credentials.token);
    return client.connect(
      (event) => this.client === client ? this.handle(event, client) : Promise.resolve(),
      (error) => { if (this.client === client) this.report(error); },
      () => { this.profilePending = true; },
    );
  }
  close(): void {
    const client = this.client;
    this.client = null;
    client?.close();
    this.keyReady = false;
  }

  /** Connecting happens in the background and retries on its own. */
  start(): void { void this.connect(); }
  stop(): void { this.close(); }
  interrupt(): void { this.close(); }

  /** Connected and the public key registered. A failed registration stays visible. */
  health(): string | null {
    if (!this.client?.connected) return "ASP is not connected";
    if (this.keyError) return this.keyError;
    if (!this.keyReady) return "public key is not registered";
    return null;
  }

  async poll(): Promise<boolean> {
    this.flushNotice();
    this.kickKey();
    // Leave messages and commands pending until the socket is up.
    if (!this.client?.connected) return false;
    this.announceProfile();
    if (!this.command && await this.pollCommand()) return true;
    return this.pollDelivery();
  }

  private flushNotice(): void {
    if (this.unspoken === null) return;
    if (messageDelivery.notify(this.bus, this.unspoken) !== null) this.unspoken = null;
  }

  /** Register once the socket is up, and again after a failure, without blocking poll. */
  private kickKey(): void {
    if (!this.client?.connected || this.keyReady || this.keyTask) return;
    if (Date.now() < this.keyRetryAt) return;
    this.keyRetryAt = Date.now() + 1_000;
    this.keyTask = this.registerKey().finally(() => { this.keyTask = null; });
  }

  private nextCardDelivery = 0;

  private async pollDelivery(): Promise<boolean> {
    if (Date.now() < this.nextCardDelivery) return false;
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
      const self = this.bus.contacts.get(MAGI_CONTACT_ID)!;
      await client.send(chat.delivery_address, message.content, { ...job.input.metadata, profile: { name: self.name, nickname: self.nickname } });
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      if (job.input.metadata?.card_status || job.input.metadata?.card || job.input.metadata?.profile) {
        board.release(this.worker_name, job.id);
        this.nextCardDelivery = Date.now() + 1_000;
      } else board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  private async pollCommand(): Promise<boolean> {
    const board = this.bus.board("AspCommandJob");
    const job = board.claim(this.worker_name, (input) => {
      if (input.name !== "sync_provider") return true;
      if (this.keyError) return true;
      return this.keyReady;
    });
    if (!job) return false;
    const id = job.id;
    const input = job.input;
    this.command = this.runCommand(id, input).finally(() => { this.command = null; });
    return true;
  }

  private async runCommand(id: number, input: AspCommandJob): Promise<void> {
    const board = this.bus.board("AspCommandJob");
    try {
      if (input.name === "sync_provider" && !this.keyReady) throw new Error(this.keyError ?? "public key is not registered");
      const output = input.name === "open_owner_chat" ? await this.openOwner() : await this.syncProvider();
      board.submit(this.worker_name, id, { output });
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      board.submit(this.worker_name, id, { error: text });
      // An open that failed has no owner chat yet. The job result is the record;
      // a sync failure still has a conversation to tell.
      if (input.name === "sync_provider") this.report(error);
    }
  }

  /** The operator sent this event, so a failure handling it is theirs to hear about. */
  private async handle(event: AspEvent, client: AspClient): Promise<Record<string, unknown> | void> {
    try {
      return await this.onEvent(event);
    } catch (error) {
      if (this.client === client) this.report(error, event.chat_id);
    }
  }

  private report(error: unknown, chat?: string): void {
    const text = `[asp] ${error instanceof Error ? error.message : String(error)}`;
    const chatId = chat ? this.bus.chats.forChannel("asp", chat).id : undefined;
    if (messageDelivery.notify(this.bus, text, chatId) === null) this.unspoken = text;
  }

  private async onEvent(event: AspEvent): Promise<Record<string, unknown> | void> {
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
          if (await this.structured(id, message)) { /* handled without a model */ }
          else if (this.asked(message)) this.ingest(id, message);
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
    const contact = handle === USER_HANDLE ? this.bus.contacts.get(SYSTEM_CONTACT_ID) : this.bus.contacts.forAspHandle(handle);
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
    if (!contact) throw new Error("chat requires an authenticated sender");
    receiveChat(this.bus, chat.id, contact.id, { content: text }, this.worker_name);
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
      this.keyError = null;
      this.keyReady = true;
    } catch (error) {
      this.keyReady = false;
      const text = error instanceof Error ? error.message : String(error);
      if (this.keyError !== text) {
        this.keyError = text;
        this.report(error);
      }
    }
  }

  /**
   * A structured field on a message: a card result, a key exchange, or a provider
   * sync. The ciphertext never becomes message text. A card result is accepted
   * only from the operator, and a provider sync only from a trusted MAGI.
   * Returns true when the message was fully handled.
   */
  private async structured(chatId: string, payload: Record<string, unknown>): Promise<boolean> {
    const metadata = payload.metadata;
    if (!isRecord(metadata)) return false;
    if (metadata.card_result === undefined && metadata.card_reject === undefined && metadata.envelope === undefined) {
      // Identity announcements are deliveries, not requests for another model turn.
      if (metadata.profile !== undefined && !this.content(payload)) return true;
      return false;
    }
    const contact = this.remember(chatId, payload.sender);
    if (!contact) throw new Error("structured chat requires an authenticated sender");
    const chat = this.bus.chats.forChannel("asp", chatId);
    receiveChat(this.bus, chat.id, contact.id, { content: "", metadata: metadata as ChatInput["metadata"] }, this.worker_name);
    return true;
  }

  /** Publish identity through an ordinary delivery after each connection. */
  private announceProfile(): void {
    if (!this.profilePending || this.profileTask || Date.now() < this.profileRetryAt) return;
    this.profilePending = false;
    const client = this.client;
    this.profileTask = (async () => {
      try {
        const { chat_id } = await this.openOwner();
        if (this.client !== client) return;
        const self = this.bus.contacts.get(MAGI_CONTACT_ID)!;
        messageDelivery.send(this.bus, chat_id, "", MAGI_CONTACT_ID, this.worker_name, { profile: { name: self.name, nickname: self.nickname } });
      } catch {
        if (this.client === client) { this.profilePending = true; this.profileRetryAt = Date.now() + 1_000; }
      }
    })().finally(() => { this.profileTask = null; });
  }

  private async openOwner(): Promise<{ chat_id: number }> {
    const client = this.requireClient();
    // ASP reuses the owner↔agent 1:1 when one already exists.
    const opened = await client.call("POST", "/chats/open", { invite: [USER_HANDLE] });
    if (!isRecord(opened) || typeof opened.chat_id !== "string") throw new Error("ASP did not open a chat");
    const chat = this.bus.chats.forChannel("asp", opened.chat_id);
    this.bus.chatMembers.add(chat.id, MAGI_CONTACT_ID);
    if (messageDelivery.homeChat(this.bus) === null) messageDelivery.setHomeChat(this.bus, chat.id);
    return { chat_id: chat.id };
  }

  private async syncProvider(): Promise<{ synced: string[] }> {
    const provider = this.bus.settings.get("provider.name")?.trim();
    const model = this.bus.settings.get("provider.model")?.trim();
    const base_url = this.bus.settings.get("provider.base_url")?.trim() ?? "";
    const api_key = this.bus.secrets.get("provider.api_key");
    if (!provider || !model || !api_key) throw new Error("provider is not configured");
    const recipients = await this.syncRecipients();
    const synced: string[] = [];
    for (const recipient of recipients) {
      const chatId = await this.chatWith(recipient.handle);
      messageDelivery.send(this.bus, chatId, `provider sync: ${provider}, ${model}, key updated`, MAGI_CONTACT_ID, this.worker_name, {
        envelope: { kind: "provider.sync", provider, model, base_url, ciphertext: encryptFor(recipient.public_key, api_key) },
      });
      synced.push(recipient.handle);
    }
    return { synced };
  }

  /** Known MAGI peers that already have a public key. A profile without one is not sent the secret. */
  private async syncRecipients(): Promise<Array<{ handle: string; public_key: string }>> {
    const deadline = Date.now() + 5_000;
    let chosen: Array<{ handle: string; public_key: string }> = [];
    let waiting = true;
    while (waiting) {
      const body = await this.requireClient().call("GET", "/profiles");
      if (!isRecord(body) || !Array.isArray(body.profiles)) throw new Error("ASP profiles were not a list");
      const magi = body.profiles.flatMap((row) => {
        if (!isRecord(row) || typeof row.handle !== "string" || !syncTarget(this.bus, row.handle)) return [];
        const public_key = typeof row.public_key === "string" && row.public_key !== "" ? row.public_key : null;
        return [{ handle: row.handle, public_key }];
      });
      chosen = magi.flatMap((row) => row.public_key === null ? [] : [{ handle: row.handle, public_key: row.public_key }]);
      waiting = chosen.length < magi.length && Date.now() < deadline;
      if (waiting) await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return chosen;
  }

  private async chatWith(handle: string): Promise<number> {
    const opened = await this.requireClient().call("POST", "/chats/open", { invite: [handle] });
    if (!isRecord(opened) || typeof opened.chat_id !== "string") throw new Error("ASP did not open a chat");
    const chat = this.bus.chats.forChannel("asp", opened.chat_id);
    this.bus.chatMembers.add(chat.id, MAGI_CONTACT_ID);
    return chat.id;
  }

  private requireClient(): AspClient {
    if (!this.client) throw new Error("ASP is not connected");
    return this.client;
  }

  /** Read at every start, so a token that arrives later is picked up by a restart. */
  private credentials(): Credentials | null {
    const base = this.override?.base ?? this.bus.settings.get("asp.base")?.trim();
    const token = this.override?.token ?? this.bus.settings.get("asp.token")?.trim();
    if (!base || !token) return null;
    return { base, token };
  }
}

/** Who may be sent this MAGI's provider key: a known MAGI, not a stranger profile. */
function syncTarget(bus: Bus, handle: string): boolean {
  if (handle === bus.handle || handle === USER_HANDLE) return false;
  if (KNOWN_MAGI.has(handle)) return true;
  return false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
