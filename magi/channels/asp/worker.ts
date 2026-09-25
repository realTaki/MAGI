import { BaseWorker, MAGI_CONTACT_ID, type Bus } from "../../bus/index.js";
import { AspClient, type AspEvent } from "./client.js";

export class AspWorker extends BaseWorker {
  readonly worker_name = "asp";
  private readonly client: AspClient;
  private readonly sessionKinds = new Map<string, Promise<string | null>>();
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
      const initial = payload.initial_message;
      if (typeof initial === "object" && initial !== null) {
        const message = initial as Record<string, unknown>;
        if (await this.shouldIngest(id, message)) this.ingest(id, message);
      }
    } else if (event.type === "session.message" && payload.sender !== this.bus.handle) {
      if (await this.shouldIngest(id, payload)) this.ingest(id, payload);
    }
    if (event.event_id) return { type: "session.ack", session_id: id, event_id: event.event_id };
  }

  private async shouldIngest(sessionId: string, payload: Record<string, unknown>): Promise<boolean> {
    if (payload.sender === this.bus.handle) return false;
    let kind = this.sessionKinds.get(sessionId);
    if (!kind) {
      kind = this.client.sessionKind(sessionId);
      this.sessionKinds.set(sessionId, kind);
    }
    try {
      // A group is a human-facing conversation, not a chain of agent-to-agent prompts.
      // Other MAGIs' replies are visible in ASP but must never start another LLM turn.
      return (await kind) !== "group" || payload.sender === "user";
    } catch (error) {
      this.sessionKinds.delete(sessionId);
      throw error;
    }
  }

  private ingest(sessionId: string, payload: Record<string, unknown>): void {
    const content = payload.content;
    const text = typeof content === "string" ? content : Array.isArray(content) ? content.map((part) => {
      if (typeof part === "object" && part !== null && "text" in part && typeof part.text === "string") return part.text;
      return "";
    }).join("") : "";
    if (text.trim()) {
      // Where the operator spoke last is the only address this workspace has for
      // reaching them, so a notice that has no conversation of its own goes there.
      if (payload.sender === "user") {
        this.bus.setHomeConversation(this.bus.conversations.forChannel("asp", sessionId).id);
      }
      this.bus.publishChat({ text: text.trim(), channel: "asp", delivery_address: sessionId }, this.worker_name);
    }
  }
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
