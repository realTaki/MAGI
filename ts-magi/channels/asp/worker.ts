import { BaseWorker, type Bus } from "../../bus/index.js";
import { AspClient, type AspEvent } from "./client.js";

export class AspWorker extends BaseWorker {
  readonly worker_name = "asp";
  private readonly client: AspClient;
  private nickname: string | null;

  constructor(bus: Bus, base: string, token: string) {
    super(bus);
    this.client = new AspClient(bus.handle, base, token);
    this.nickname = bus.getSetting("asp.nickname");
  }

  connect(): Promise<void> { return this.client.connect((event) => this.onEvent(event)); }
  close(): void { this.client.close(); }

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

  private async onEvent(event: AspEvent): Promise<Record<string, unknown> | void> {
    if (event.type === "agent.nickname.read") return { type: "agent.nickname.current", nickname: this.nickname };
    if (event.type === "agent.nickname.update") {
      const updated = typeof event.nickname === "string" && !!event.nickname.trim();
      if (updated) {
        this.nickname = event.nickname!.trim();
        this.bus.setSetting("asp.nickname", this.nickname);
      }
      return { type: "agent.nickname.updated", request_id: event.request_id, ok: updated };
    }
    const id = event.session_id;
    if (!id) return;
    const payload = event.payload ?? {};
    if (event.type === "session.invited" && payload.invitee === this.bus.handle) {
      await this.client.join(id);
      const initial = payload.initial_message;
      if (typeof initial === "object" && initial !== null) this.ingest(id, initial as Record<string, unknown>);
    } else if (event.type === "session.message" && payload.sender !== this.bus.handle) {
      this.ingest(id, payload);
    }
  }

  private ingest(sessionId: string, payload: Record<string, unknown>): void {
    const content = payload.content;
    const text = typeof content === "string" ? content : Array.isArray(content) ? content.map((part) => {
      if (typeof part === "object" && part !== null && "text" in part && typeof part.text === "string") return part.text;
      return "";
    }).join("") : "";
    if (text.trim()) this.bus.publishChat({ text: text.trim(), channel: "asp", delivery_address: sessionId }, this.worker_name);
  }
}
