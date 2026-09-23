import { BaseWorker, type Bus, type LLMTool } from "../bus/index.js";
import { Conversation } from "./conversations.js";

export class AgentWorker extends BaseWorker {
  readonly worker_name = "agent";
  private readonly queues = new Map<number, Promise<void>>();

  constructor(bus: Bus, private readonly tools: () => LLMTool[]) { super(bus); }

  async poll(): Promise<boolean> {
    const job = this.bus.board("ChatNotify").claim(this.worker_name);
    if (!job) return false;
    const id = job.input.conversation_id;
    if (!id) {
      this.bus.board("ChatNotify").submit(this.worker_name, job.id, { error: "conversation_id is missing" });
      return true;
    }
    const previous = this.queues.get(id) ?? Promise.resolve();
    const next = previous.then(() => new Conversation(this.bus, id, this.tools).run(job.id));
    this.queues.set(id, next);
    void next.finally(() => { if (this.queues.get(id) === next) this.queues.delete(id); });
    return true;
  }

  async drain(): Promise<void> { await Promise.all(this.queues.values()); }
}
