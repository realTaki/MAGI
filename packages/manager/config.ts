/**
 * Cards, config, and decryption. The manager supervises this worker; it does
 * not do the work itself.
 *
 * A provider key is verified, through the providers worker, before it is stored.
 * A failed startup card is offered again.
 */

import {
  BaseWorker,
  CARD_RESTART_ERROR,
  decryptWith,
  MAGI_CONTACT_ID,
  messageDelivery,
  providerCard,
  SYSTEM_CONTACT_ID,
  waitForJob,
  type Bus,
  type ChangeProviderNotify,
  type ConfigureJob,
  type InboundSecretJob,
} from "@magi/bus";

export class ConfigWorker extends BaseWorker {
  readonly worker_name = "config";
  /** The startup provider card, while it is waiting for the user. */
  private cardJob: number | null = null;
  /** The owner 1:1 this worker uses for the card. ASP reuses that room if it exists. */
  private ownerChat: number | null = null;
  /** The open-chat job, while ASP is being asked for a home chat. */
  private opening: number | null = null;
  private openAgainAt = 0;
  /** How many times the startup card has been sent. A restart does not add to this. */
  private offers = 0;
  private cardStopped = false;
  /** Inbound card and sync work, off this worker's poll so config writes can finish. */
  private task: Promise<void> | null = null;
  private unspoken: string | null = null;

  constructor(bus: Bus) {
    super(bus);
    this.offers = Number(bus.settings.get("provider.card.offers") ?? "0") || 0;
    this.cardStopped = bus.settings.get("provider.card.stopped") === "1";
    const stored = Number(bus.settings.get("provider.card.job") ?? "");
    this.cardJob = Number.isInteger(stored) && stored > 0 ? stored : null;
  }

  async poll(): Promise<boolean> {
    this.flushNotice();
    if (await this.pollConfigure()) return true;
    if (this.task) return false;
    if (await this.pollInbound()) return true;
    await this.providerSetup();
    return false;
  }

  private flushNotice(): void {
    if (this.unspoken === null || this.ownerChat === null) return;
    if (messageDelivery.notify(this.bus, this.unspoken, this.ownerChat) !== null) this.unspoken = null;
  }

  private tell(text: string, chatId?: number): void {
    const target = chatId ?? this.ownerChat;
    if (target === null || messageDelivery.notify(this.bus, text, target) === null) this.unspoken = text;
  }

  /** No provider key means this node cannot think. Show the card from here. */
  private async providerSetup(): Promise<void> {
    if (this.bus.secrets.get("provider.api_key")) {
      if (this.offers !== 0 || this.cardStopped || this.rejections() !== 0 || this.bus.settings.get("provider.card.resent") === "1") {
        this.offers = 0;
        this.cardStopped = false;
        this.bus.settings.set("provider.card.offers", "0");
        this.bus.settings.set("provider.card.rejections", "0");
        this.bus.settings.set("provider.card.resent", "");
        this.bus.settings.set("provider.card.stopped", "");
        this.bus.settings.set("provider.card.job", "");
      }
      return;
    }
    if (this.cardStopped || this.rejections() >= 2) {
      this.stopCard(this.cardStopped ? null : "provider card was rejected");
      return;
    }
    const open = this.cardJob === null ? null : this.bus.board("CardJob").result(this.cardJob);
    if (this.cardJob !== null && !open) return;
    // The bus fails a card that was still open when the process exited. That is not the user.
    const restarted = open?.error === CARD_RESTART_ERROR;
    if (open && !restarted) {
      this.forgetCard();
      if (open.status === "completed") {
        this.announce(open.output?.fields ?? {});
        return;
      }
      if (this.noteRejection(open.error ?? "provider card failed")) return;
    }
    if (!restarted && this.bus.settings.get("provider.card.resent") === "1") return;
    const chatId = this.ensureOwnerChat();
    if (chatId === null) return;
    const recovering = restarted || (this.offers > 0 && this.cardJob === null && this.rejections() === 0);
    if (!recovering) {
      this.offers += 1;
      this.bus.settings.set("provider.card.offers", String(this.offers));
      if (this.rejections() === 1) this.bus.settings.set("provider.card.resent", "1");
    }
    const spec = providerCard(this.bus.handle);
    this.cardJob = this.bus.board("CardJob").publish({ spec }, this.worker_name);
    this.bus.settings.set("provider.card.job", String(this.cardJob));
    messageDelivery.send(this.bus, chatId, `card: ${spec.title}`, MAGI_CONTACT_ID, this.worker_name, { card: spec });
  }

  private forgetCard(): void {
    this.cardJob = null;
    this.bus.settings.set("provider.card.job", "");
  }

  private rejections(): number {
    return Number(this.bus.settings.get("provider.card.rejections") ?? "0") || 0;
  }

  /** Count a rejection. True when the card must not be sent again. */
  private noteRejection(reason: string): boolean {
    const count = this.rejections() + 1;
    this.bus.settings.set("provider.card.rejections", String(count));
    if (count < 2 && this.offers < 2) return false;
    this.stopCard(reason);
    return true;
  }

  private stopCard(reason: string | null): void {
    if (this.cardStopped) return;
    this.cardStopped = true;
    this.bus.settings.set("provider.card.stopped", "1");
    if (reason) this.tell(`[provider] ${reason}. The card will not be shown again.`);
  }

  /** The startup card is shown only in a chat this worker opened. */
  private ensureOwnerChat(): number | null {
    if (this.ownerChat !== null) return this.ownerChat;
    if (!this.bus.settings.get("asp.base")?.trim()) return null;
    if (Date.now() < this.openAgainAt) return null;
    if (this.opening === null) {
      this.opening = this.bus.board("AspCommandJob").publish({ name: "open_owner_chat" }, this.worker_name);
      return null;
    }
    const opened = this.bus.board("AspCommandJob").result(this.opening);
    if (!opened) return null;
    this.opening = null;
    const output = opened.output;
    if (opened.status === "failed" || output === undefined || !("chat_id" in output)) {
      this.openAgainAt = Date.now() + 1_000;
      this.tell(`[provider] ${opened.error ?? "could not open a chat for the provider card"}`);
      return null;
    }
    this.ownerChat = output.chat_id;
    return this.ownerChat;
  }

  private announce(fields: Record<string, string>): void {
    const chatId = this.ownerChat ?? messageDelivery.presentChat(this.bus);
    if (chatId === null) return;
    messageDelivery.send(this.bus, chatId, [
      "Provider card submitted.",
      `provider: ${fields.provider ?? ""}`,
      `model: ${fields.model ?? ""}`,
      `base_url: ${fields.base_url ?? ""}`,
      "provider.api_key: updated",
      "Load provider_setup and continue from step 2.",
    ].join("\n"), SYSTEM_CONTACT_ID, this.worker_name);
  }

  private async pollConfigure(): Promise<boolean> {
    const board = this.bus.board("ConfigureJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    try {
      const written = this.configure(job.input);
      board.submit(this.worker_name, job.id, { output: { secret_set: written } });
    } catch (error) {
      const text = message(error);
      board.submit(this.worker_name, job.id, { error: text });
      this.tell(`[provider] ${text}`);
    }
    return true;
  }

  private configure(input: ConfigureJob): string[] {
    const written: string[] = [];
    for (const [key, value] of Object.entries(input.settings ?? {})) this.bus.settings.set(key, value);
    for (const [key, value] of Object.entries(input.secrets ?? {})) {
      this.bus.secrets.set(key, value);
      written.push(key);
    }
    for (const [key, ciphertext] of Object.entries(input.sealed ?? {})) {
      this.bus.secrets.set(key, this.decrypt(key, ciphertext));
      written.push(key);
    }
    return written;
  }

  private async pollInbound(): Promise<boolean> {
    const board = this.bus.board("InboundSecretJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    this.task = this.finishInbound(job.id, job.input).finally(() => { this.task = null; });
    return true;
  }

  private async finishInbound(id: number, input: InboundSecretJob): Promise<void> {
    const board = this.bus.board("InboundSecretJob");
    try {
      if (input.kind === "card_result") await this.applyCard(input);
      else if (input.kind === "card_reject") await this.rejectCard(input);
      else await this.applySync(input);
      board.submit(this.worker_name, id, { output: { applied: true } });
    } catch (error) {
      const text = message(error);
      board.submit(this.worker_name, id, { error: text });
      this.tell(`[provider] ${text}`, input.chat_id);
    }
  }

  private async applyCard(input: InboundSecretJob): Promise<void> {
    const result = input.body;
    if (typeof result.id !== "string") throw new Error("card result is missing an id");
    const board = this.bus.board("CardJob");
    const job = board.claim(this.worker_name, (card) => card.spec.id === result.id);
    if (!job) throw new Error(`no open card ${result.id}`);
    try {
      const fields = isRecord(result.fields) ? result.fields : {};
      const visible: Record<string, string> = {};
      const sealed: Record<string, string> = {};
      let providerKey: string | undefined;
      for (const field of job.input.spec.fields) {
        const value = fields[field.name];
        if (field.kind === "secret") {
          if (!isRecord(value) || typeof value.ciphertext !== "string" || value.ciphertext === "") {
            if (field.optional) continue;
            throw new Error(`card field ${field.name} is missing a ciphertext`);
          }
          if (field.name === "provider.api_key") providerKey = this.decrypt(field.name, value.ciphertext);
          else sealed[field.name] = value.ciphertext;
          visible[field.name] = "updated";
        } else if (typeof value === "string" && value !== "") {
          visible[field.name] = value;
        } else if (!field.optional) {
          throw new Error(`card field ${field.name} is missing a value`);
        }
      }
      if (providerKey !== undefined) {
        await this.useProvider({
          provider: visible.provider,
          model: visible.model,
          base_url: visible.base_url,
          api_key: providerKey,
        });
      }
      for (const [key, ciphertext] of Object.entries(sealed)) {
        this.bus.secrets.set(key, this.decrypt(key, ciphertext));
      }
      board.submit(this.worker_name, job.id, { output: { fields: visible } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: message(error) });
      throw error;
    }
  }

  private async rejectCard(input: InboundSecretJob): Promise<void> {
    const id = typeof input.body.id === "string" ? input.body.id : "";
    if (id === "") throw new Error("card rejection is missing an id");
    const board = this.bus.board("CardJob");
    const job = board.claim(this.worker_name, (card) => card.spec.id === id);
    if (!job) throw new Error(`no open card ${id}`);
    board.submit(this.worker_name, job.id, { error: "rejected" });
  }

  private async applySync(input: InboundSecretJob): Promise<void> {
    const envelope = input.body;
    if (typeof envelope.ciphertext !== "string" || envelope.ciphertext === "") {
      throw new Error("provider sync is missing a ciphertext");
    }
    const api_key = this.decrypt("provider.api_key", envelope.ciphertext);
    await this.useProvider({
      provider: stringField(envelope.provider),
      model: stringField(envelope.model),
      base_url: stringField(envelope.base_url),
      api_key,
    });
  }

  /** Verify, then store. The providers worker rejects a bad key before ConfigureJob runs. */
  private async useProvider(change: ChangeProviderNotify): Promise<void> {
    const id = this.bus.board("ChangeProviderNotify").publish(change, this.worker_name);
    let result;
    try {
      result = await waitForJob(() => this.bus.board("ChangeProviderNotify").result(id), 30_000, "provider verify");
    } catch (error) {
      throw new Error(message(error));
    }
    if (result.status === "failed") throw new Error(result.error ?? "provider was rejected");
  }

  private decrypt(key: string, ciphertext: string): string {
    try {
      return decryptWith(this.bus.identity.privateKey, ciphertext);
    } catch {
      throw new Error(`could not decrypt ${key}`);
    }
  }
}

function stringField(value: unknown): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
