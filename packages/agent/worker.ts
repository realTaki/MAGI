import { BaseWorker, MAGI_CONTACT_ID, type Bus } from "@magi/bus";
import { Chat } from "./chats.js";
import { PROMPT_DEFAULTS } from "./prompt_defaults.js";

export class AgentWorker extends BaseWorker {
  readonly worker_name = "agent";
  private readonly queues = new Map<number, Promise<void>>();

  constructor(bus: Bus) {
    super(bus);
    for (const [key, value] of PROMPT_DEFAULTS) bus.prompts.register(key, value);
  }

  async poll(): Promise<boolean> {
    const board = this.bus.board("MessageDeliveryJob");
    // What someone else said is this MAGI's to answer. Its own words are not: they are
    // for a channel to deliver, and nobody answers them here.
    // A missing message is also claimed here so the invalid job is failed rather than
    // left pending forever.
    const job = board.claim(this.worker_name, (input) => this.bus.messages.get(input.message_id)?.contact_id !== MAGI_CONTACT_ID);
    if (!job) return false;
    const message = this.bus.messages.get(job.input.message_id);
    if (!message) {
      board.submit(this.worker_name, job.id, { error: `message ${job.input.message_id} does not exist` });
      return true;
    }
    const id = message.chat_id;
    const previous = this.queues.get(id) ?? Promise.resolve();
    const next = previous.then(() => new Chat(this.bus, id).run(job.id));
    this.queues.set(id, next);
    void next.finally(() => { if (this.queues.get(id) === next) this.queues.delete(id); });
    return true;
  }

  async drain(): Promise<void> { await Promise.all(this.queues.values()); }

  /** Stopping the agent means letting the turns it already accepted finish. */
  async stop(): Promise<void> { await this.drain(); }
}
