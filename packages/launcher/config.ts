import type { OAuthSession, OAuthSessionFactory, OAuthUpdate } from "./oauth-port.js";
import { providerFailureCode } from "@magi/bus/jobs/providerFailure.js";
import type { CardReceipt } from "@magi/bus/jobs/card.js";
/**
 * Cards, config, and decryption. The manager supervises this worker; it does
 * not do the work itself.
 *
 * A provider key is verified, through the providers worker, before it is stored.
 * A failed card can be reopened without a working model.
 */

import {
  BaseWorker,
  CARD_RESTART_ERROR,
  decryptWith,
  MAGI_CONTACT_ID,
  messageDelivery,
  providerCard,
  type Bus,
  type ChangeProviderNotify,
  type ConfigureJob,
  type InboundSecretJob,
} from "@magi/bus";

export class ConfigWorker extends BaseWorker {
  readonly worker_name = "config";
  /** The startup provider card, while it is waiting for the user. */
  private cardJob: number | null = null;
  /** The authenticated local owner conversation used for recovery cards. */
  private ownerChat: number | null = null;
  private cardStopped = false;
  /** Inbound card and sync work, off this worker's poll so config writes can finish. */
  private task: Promise<void> | null = null;
  private unspoken: string | null = null;
  private readonly oauthSession?: OAuthSession;
  private get oauth(): OAuthSession {
    if (!this.oauthSession) throw new Error("OAuthError:unavailable");
    return this.oauthSession;
  }

  constructor(bus: Bus, oauthFactory?: OAuthSessionFactory) {
    super(bus);
    this.oauthSession = oauthFactory?.(bus, async (ref, model, signal) => {
      if (bus.settings.get("node.enabled") === "false") throw new Error("OAuthError:paused");
      await this.useProvider({ provider: "openai-codex", model, base_url: "", oauth_ref: ref }, signal);
    }, (binding, update) => {
      bus.settings.set(`provider.oauth.attempt.${binding.attempt}`, JSON.stringify({ binding, update: { ...update, device: undefined } }));
      messageDelivery.send(bus, binding.chat, "Provider connection: " + update.state, MAGI_CONTACT_ID, this.worker_name, { provider_oauth: update });
    });
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
    if (this.bus.secrets.get("provider.api_key") || this.bus.secrets.get(this.bus.settings.get("provider.oauth_ref") ?? "")) {
      return;
    }
    if (this.cardStopped) return;
    const open = this.cardJob === null ? null : this.bus.board("CardJob").result(this.cardJob);
    if (this.cardJob !== null && !open) return;
    if (open && open.error !== CARD_RESTART_ERROR) {
      this.forgetCard();
      if (open.status !== "completed") {
        this.cardStopped = true;
        this.bus.settings.set("provider.card.stopped", "1");
      }
      return;
    }
    const chatId = this.ensureOwnerChat();
    if (chatId === null) return;
    const spec = providerCard(this.bus.handle, {
      provider: this.bus.settings.get("provider.name") ?? undefined,
      model: this.bus.settings.get("provider.model") ?? undefined,
      base_url: this.bus.settings.get("provider.base_url") ?? undefined,
    });
    spec.public_key = this.bus.identity.publicKey;
    this.bus.settings.set("provider.card.spec", JSON.stringify(spec));
    this.cardJob = this.bus.board("CardJob").publish({ spec }, this.worker_name);
    this.bus.settings.set("provider.card.job", String(this.cardJob));
    messageDelivery.send(this.bus, chatId, `card: ${spec.title}`, MAGI_CONTACT_ID, this.worker_name, { card: spec });
  }

  private forgetCard(): void {
    this.cardJob = null;
    this.bus.settings.set("provider.card.job", "");
  }

  /** The startup card is shown only in a chat this worker opened. */
  private ensureOwnerChat(): number | null {
    if (this.ownerChat !== null) return this.ownerChat;
    this.ownerChat = messageDelivery.presentChat(this.bus);
    return this.ownerChat;
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
      messageDelivery.reportError(this.bus, text, { module: this.worker_name, source: `ConfigureJob:${job.id}` });
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
      if (input.kind === "provider.oauth") this.applyOAuth(input);
      else if (input.kind === "card_result") await this.applyCard(input);
      else if (input.kind === "card_reject") await this.rejectCard(input);
      else await this.applySync(input);
      board.submit(this.worker_name, id, { output: { applied: true } });
    } catch (error) {
      const text = message(error);
      board.submit(this.worker_name, id, { error: text });
      messageDelivery.reportError(this.bus, text, { module: this.worker_name, source: `InboundSecretJob:${id}`, chat_id: input.chat_id });
    }
  }

  private applyOAuth(input: InboundSecretJob): void {
    // Only chat() can admit this structured command from an authenticated owner.
    const target = this.bus.chats.get(input.chat_id);
    if (!target || !["shell-owner", "local-owner"].includes(target.channel)) throw new Error("OAuthError:owner");
    const binding = { instance: this.bus.identity.publicKey, owner: this.bus.userContactId, chat: input.chat_id, attempt: String(input.body.attempt ?? "") };
    if (input.body.action === "start") {
      const model = typeof input.body.model === "string" ? input.body.model : this.bus.settings.get("provider.model");
      if (!model) throw new Error("OAuthError:model_required");
      const old = this.bus.settings.get(`provider.oauth.attempt.${binding.attempt}`);
      if (old) { this.oauthStatusAfterRestart(binding, old); return; }
      if (this.bus.settings.get("node.enabled") === "false") {
        const update: OAuthUpdate = { attempt: binding.attempt, instance: binding.instance, state: "failed" };
        this.bus.settings.set(`provider.oauth.attempt.${binding.attempt}`, JSON.stringify({ binding, update }));
        messageDelivery.send(this.bus, binding.chat, "Provider connection: failed", MAGI_CONTACT_ID, this.worker_name, { provider_oauth: update });
        return;
      }
      this.oauth.start(binding, model);
    } else if (input.body.action === "cancel") this.oauth.cancel(binding);
    else if (input.body.action === "status") {
      try { this.oauth.status(binding); }
      catch {
        const old = this.bus.settings.get(`provider.oauth.attempt.${binding.attempt}`);
        if (!old) throw new Error("OAuthError:attempt");
        this.oauthStatusAfterRestart(binding, old);
      }
    }
    else throw new Error("OAuthError:request");
  }

  private oauthStatusAfterRestart(binding: { instance: string; owner: number; chat: number; attempt: string }, raw: string): void {
    const old = JSON.parse(raw) as { binding: typeof binding; update: OAuthUpdate };
    if (JSON.stringify(old.binding) !== JSON.stringify(binding)) throw new Error("OAuthError:attempt");
    // Never repeat an authorization request due to replay or process restart.
    let update = old.update;
    if (update.state === "pending" || update.state === "verifying") {
      try { this.oauth.status(binding); return; } catch { update = { ...update, state: "cancelled" }; }
    }
    messageDelivery.send(this.bus, binding.chat, "Provider connection: " + update.state, MAGI_CONTACT_ID, this.worker_name, { provider_oauth: update });
  }

  interrupt(): void { this.oauthSession?.stop(); }
  async stop(): Promise<void> { this.oauthSession?.stop(); await this.oauthSession?.settled(); }

  private async applyCard(input: InboundSecretJob): Promise<void> {
    const result = input.body;
    if (typeof result.id !== "string") throw new Error("card result is missing an id");
    if (result.action === "open") {
      const requestKey = `card.open.${result.id}`;
      if (this.bus.settings.get(requestKey)) return;
      if (this.cardJob !== null && !this.bus.board("CardJob").result(this.cardJob)) {
        const current = this.bus.settings.get("provider.card.spec");
        if (current) {
          const spec = JSON.parse(current) as import("@magi/bus").CardSpec;
          this.bus.settings.set(requestKey, spec.id);
          messageDelivery.send(this.bus, input.chat_id, "Provider configuration", MAGI_CONTACT_ID, this.worker_name, { card: spec });
          const status = this.bus.settings.get(`card.receipt.${spec.id}`);
          if (status) this.deliverReceipt(input.chat_id, JSON.parse(status) as CardReceipt);
          return;
        }
      }
      const spec = providerCard(this.bus.handle, {
        provider: this.bus.settings.get("provider.name") ?? undefined,
        model: this.bus.settings.get("provider.model") ?? undefined,
        base_url: this.bus.settings.get("provider.base_url") ?? undefined,
      });
      // Profile configuration is available without any model call.
      if (this.cardJob !== null && this.bus.board("CardJob").fail(this.cardJob, "superseded")) {
        const previous = this.bus.settings.get("provider.card.spec");
        if (previous) {
          const old = JSON.parse(previous) as import("@magi/bus").CardSpec;
          this.cardReceipt(input.chat_id, old, "cancelled", old.values ?? {});
        }
      }
      spec.public_key = this.bus.identity.publicKey;
      this.bus.settings.set("provider.card.spec", JSON.stringify(spec));
      const id = this.bus.board("CardJob").publish({ spec }, this.worker_name);
      this.cardJob = id;
      this.cardStopped = false;
      this.bus.settings.set("provider.card.job", String(id));
      this.bus.settings.set("provider.card.stopped", "");
      this.bus.settings.set(requestKey, spec.id);
      messageDelivery.send(this.bus, input.chat_id, "card: Provider", MAGI_CONTACT_ID, this.worker_name, { card: spec });
      return;
    }
    const receiptKey = `card.receipt.${result.id}`;
    const receipt = this.bus.settings.get(receiptKey);
    if (result.action === "status") {
      const current = receipt ? JSON.parse(receipt) as CardReceipt : undefined;
      if (typeof result.attempt === "string" && current?.attempt !== result.attempt && current?.state !== "saved" && current?.state !== "cancelled") {
        this.deliverReceipt(input.chat_id, { id: result.id, attempt: result.attempt, state: "failed", error: "connection", values: current?.values });
      } else if (current) this.deliverReceipt(input.chat_id, current);
      return;
    }
    const saved = receipt ? JSON.parse(receipt) as CardReceipt : undefined;
    if (saved?.state === "saved" || saved?.state === "cancelled") {
      this.deliverReceipt(input.chat_id, saved);
      return;
    }
    if (typeof result.attempt !== "string" || !result.attempt) throw new Error("card submission is missing its attempt id");
    const attemptKey = `card.attempt.${result.id}.${result.attempt}`;
    const previous = this.bus.settings.get(attemptKey);
    if (previous) {
      // A replay acknowledges the authoritative current attempt, never starts another request.
      if (receipt) this.deliverReceipt(input.chat_id, JSON.parse(receipt) as CardReceipt);
      return;
    }
    const board = this.bus.board("CardJob");
    const job = board.claim(this.worker_name, (card) => card.spec.id === result.id);
    if (!job) throw new Error(`no open card ${result.id}`);
    const visible: Record<string, string> = {};
    try {
      const fields = isRecord(result.fields) ? result.fields : {};
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
        } else if (!field.optional && !(field.name === "base_url" && typeof fields.provider === "string" && fields.provider !== "custom")) {
          throw new Error(`card field ${field.name} is missing a value`);
        }
      }
      this.cardReceipt(input.chat_id, job.input.spec, "pending", visible, result.attempt);
      if (providerKey !== undefined) {
        await this.useProvider({
          provider: visible.provider,
          model: visible.model,
          base_url: visible.provider === "custom" ? visible.base_url : "",
          api_key: providerKey,
        });
      }
      for (const [key, ciphertext] of Object.entries(sealed)) {
        this.bus.secrets.set(key, this.decrypt(key, ciphertext));
      }
      board.submit(this.worker_name, job.id, { output: { fields: visible } });
      this.cardReceipt(input.chat_id, job.input.spec, "saved", visible, result.attempt);
    } catch (error) {
      if (job.input.spec.purpose === "provider") {
        board.release(this.worker_name, job.id);
        this.cardReceipt(input.chat_id, job.input.spec, "failed", visible, result.attempt, providerFailureCode(error));
        // The form remains editable. No separate model turn, retry count or replacement card is needed.
      } else {
        board.submit(this.worker_name, job.id, { error: message(error) });
        this.cardReceipt(input.chat_id, job.input.spec, "failed", visible, result.attempt, "unknown");
      }
      throw error;
    }
  }

  private async rejectCard(input: InboundSecretJob): Promise<void> {
    const id = typeof input.body.id === "string" ? input.body.id : "";
    if (id === "") throw new Error("card rejection is missing an id");
    const receipt = this.bus.settings.get(`card.receipt.${id}`);
    if (receipt && ["saved", "cancelled"].includes((JSON.parse(receipt) as CardReceipt).state)) return;
    const board = this.bus.board("CardJob");
    const job = board.claim(this.worker_name, (card) => card.spec.id === id);
    if (!job) throw new Error(`no open card ${id}`);
    board.submit(this.worker_name, job.id, { error: "rejected" });
    this.cardReceipt(input.chat_id, job.input.spec, "cancelled", job.input.spec.values ?? {});
    if (job.id === this.cardJob) {
      this.cardStopped = true;
      this.bus.settings.set("provider.card.stopped", "1");
    }
  }

  private cardReceipt(chatId: number, spec: import("@magi/bus").CardSpec, state: CardReceipt["state"], fields: Record<string, string | undefined>, attempt?: string, error?: CardReceipt["error"]): void {
    const values = spec.purpose === "provider" ? { provider: fields.provider, model: fields.model, base_url: fields.base_url } : undefined;
    const receipt: CardReceipt = { id: spec.id, state, attempt, error, values };
    this.bus.settings.set(`card.receipt.${spec.id}`, JSON.stringify(receipt));
    if (attempt) this.bus.settings.set(`card.attempt.${spec.id}.${attempt}`, JSON.stringify(receipt));
    this.deliverReceipt(chatId, receipt);
  }

  private deliverReceipt(chatId: number, receipt: CardReceipt): void {
    messageDelivery.send(this.bus, chatId, `card: ${receipt.state}`, MAGI_CONTACT_ID, this.worker_name, { card_status: receipt });
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
    this.tell("provider sync: key updated", input.chat_id);
  }

  /** Verify, then store. The providers worker rejects a bad key before ConfigureJob runs. */
  private async useProvider(change: ChangeProviderNotify, signal?: AbortSignal): Promise<void> {
    const id = this.bus.board("ChangeProviderNotify").publish(change, this.worker_name);
    // The providers worker owns its bounded verification/write timeouts. A second,
    // shorter timeout here could declare failure while that worker later saves.
    const admittedBy = Date.now() + 30_000;
    let result = this.bus.board("ChangeProviderNotify").result(id);
    while (!result) {
      // Cancel only unclaimed OAuth work: never time out an already-owned commit.
      if (change.oauth_ref && (this.bus.settings.get("node.enabled") === "false" || Date.now() >= admittedBy) && this.bus.board("ChangeProviderNotify").failPending(id, "Provider unavailable")) throw new Error("OAuthError:unavailable");
      if (signal?.aborted) { this.bus.board("ChangeProviderNotify").fail(id, "Provider connection interrupted"); throw new Error("OAuthError:interrupted"); }
      await new Promise((resolve) => setTimeout(resolve, 20));
      result = this.bus.board("ChangeProviderNotify").result(id);
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
