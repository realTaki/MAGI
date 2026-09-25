import { BaseWorker, type Bus } from "../../bus/index.js";

type Update = { update_id: number; message?: { text?: string; chat?: { id?: number } } };

/** What a Telegram bot needs; a MAGI may not have one until someone sets it. */
type Credentials = { token: string; apiBase: string };

export class TelegramWorker extends BaseWorker {
  readonly worker_name = "tg";
  private offset: number;
  private listening = false;
  private listenTask: Promise<void> | null = null;
  private abort = new AbortController();
  private apiBase = "";
  private lastError: string | null = null;

  /** `override` is for running a MAGI by hand; normally the settings have the token. */
  constructor(bus: Bus, private readonly override?: { token: string; apiBase?: string }) {
    super(bus);
    this.offset = Number(bus.settings.get("telegram.offset") ?? 0) || 0;
  }

  /** No token, nothing to do — the manager asks this before starting the worker. */
  configured(): boolean { return this.credentials() !== null; }

  /** The listener is what can go wrong quietly, so the manager asks it regularly. */
  health(): string | null { return this.lastError; }

  start(): void {
    const credentials = this.credentials();
    if (credentials === null || this.listening) return;
    this.apiBase = credentials.apiBase;
    this.listening = true;
    this.abort = new AbortController();
    this.listenTask = this.listen();
  }

  async stop(): Promise<void> {
    this.listening = false;
    // Long polling means the listener sits inside a request for seconds at a time;
    // aborting it is what makes stopping (or a new token) take effect at once.
    this.abort.abort();
    await this.listenTask;
    this.listenTask = null;
  }

  async poll(): Promise<boolean> {
    const board = this.bus.board("DeliveryNotify");
    const job = board.claim(this.worker_name, (input) => input.channel === "tg");
    if (!job) return false;
    try {
      const address = Number(job.input.address);
      if (!Number.isInteger(address) || !address) throw new Error("delivery has no Telegram chat");
      await this.post("sendMessage", { chat_id: address, text: job.input.text });
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  /** Read at every start, so a token that arrives later is picked up by a restart. */
  private credentials(): Credentials | null {
    const token = this.override?.token ?? this.bus.settings.get("telegram.bot_token")?.trim();
    if (!token) return null;
    const apiBase = this.override?.apiBase ?? this.bus.settings.get("telegram.api_base")?.trim();
    return { token, apiBase: apiBase || `https://api.telegram.org/bot${token}` };
  }

  private async listen(): Promise<void> {
    while (this.listening) {
      try {
        const body = await this.post("getUpdates", { offset: this.offset, timeout: 10 });
        this.lastError = null;
        const updates = Array.isArray(body.result) ? body.result as Update[] : [];
        for (const update of updates) {
          this.offset = Math.max(this.offset, update.update_id + 1);
          this.bus.settings.set("telegram.offset", String(this.offset));
          const chatId = update.message?.chat?.id;
          const text = update.message?.text?.trim();
          if (chatId !== undefined && text) this.bus.publishChat({ text, channel: "tg", delivery_address: String(chatId) }, this.worker_name);
        }
      } catch (error) {
        if (this.listening) {
          this.lastError = error instanceof Error ? error.message : String(error);
          console.error("Telegram listener:", error);
          await Bun.sleep(1_000);
        }
      }
    }
  }

  private async post(method: string, payload: Record<string, unknown>): Promise<{ ok?: boolean; result?: unknown }> {
    const response = await fetch(`${this.apiBase}/${method}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      signal: AbortSignal.any([AbortSignal.timeout(15_000), this.abort.signal]),
    });
    if (!response.ok) throw new Error(`Telegram HTTP ${response.status}`);
    const body = await response.json() as { ok?: boolean; result?: unknown };
    if (!body.ok) throw new Error(`Telegram ${method} failed`);
    return body;
  }
}
