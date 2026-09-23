import { BaseWorker, type Bus } from "../../bus/index.js";

type Update = { update_id: number; message?: { text?: string; chat?: { id?: number } } };

export class TelegramWorker extends BaseWorker {
  readonly worker_name = "tg";
  private offset: number;
  private listening = false;
  private listenTask: Promise<void> | null = null;

  constructor(bus: Bus, private readonly token: string, private readonly apiBase = `https://api.telegram.org/bot${token}`) {
    super(bus);
    this.offset = Number(bus.getSetting("telegram.offset") ?? 0) || 0;
  }

  start(): void {
    if (!this.token || this.listening) return;
    this.listening = true;
    this.listenTask = this.listen();
  }

  async stop(): Promise<void> {
    this.listening = false;
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

  private async listen(): Promise<void> {
    while (this.listening) {
      try {
        const body = await this.post("getUpdates", { offset: this.offset, timeout: 10 });
        const updates = Array.isArray(body.result) ? body.result as Update[] : [];
        for (const update of updates) {
          this.offset = Math.max(this.offset, update.update_id + 1);
          this.bus.setSetting("telegram.offset", String(this.offset));
          const chatId = update.message?.chat?.id;
          const text = update.message?.text?.trim();
          if (chatId !== undefined && text) this.bus.publishChat({ text, channel: "tg", delivery_address: String(chatId) }, this.worker_name);
        }
      } catch (error) {
        if (this.listening) {
          console.error("Telegram listener:", error);
          await Bun.sleep(1_000);
        }
      }
    }
  }

  private async post(method: string, payload: Record<string, unknown>): Promise<{ ok?: boolean; result?: unknown }> {
    const response = await fetch(`${this.apiBase}/${method}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) throw new Error(`Telegram HTTP ${response.status}`);
    const body = await response.json() as { ok?: boolean; result?: unknown };
    if (!body.ok) throw new Error(`Telegram ${method} failed`);
    return body;
  }
}
