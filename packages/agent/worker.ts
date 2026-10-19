import { BaseWorker, MAGI_CONTACT_ID, type Bus } from "@magi/bus";
import { Chat } from "./chats.js";
import { sourceCheckout } from "./checkout.js";
import { PROMPT_DEFAULTS } from "./prompt_defaults.js";

export class AgentWorker extends BaseWorker {
  readonly worker_name = "agent";
  private readonly queues = new Map<number, Promise<void>>();
  private started = false;
  private readonly active = new Set<AbortController>();
  private generation = 0;

  constructor(bus: Bus) {
    super(bus);
    for (const [key, value] of PROMPT_DEFAULTS) bus.prompts.register(key, value);
    // Present only while this worker is up, same as the other live blocks.
    bus.prompts.registerSource("agent/source", "Source checkout", () =>
      this.started ? sourceCheckout(bus.handle, bus.workspace) : "");
  }

  async start(): Promise<void> {
    this.started = true;
  }

  async poll(): Promise<boolean> {
    if (!this.started) return false;
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
    const generation = this.generation;
    const next = previous.then(async () => {
      if (generation !== this.generation) { board.submit(this.worker_name, job.id, { error: "MAGI stopped" }); return; }
      const controller = new AbortController();
      this.active.add(controller);
      try { await new Chat(this.bus, id, controller.signal).run(job.id); }
      finally { this.active.delete(controller); }
    });
    this.queues.set(id, next);
    void next.finally(() => { if (this.queues.get(id) === next) this.queues.delete(id); });
    return true;
  }

  interrupt(): void {
    this.started = false;
    this.generation++;
    for (const controller of this.active) controller.abort(new Error("MAGI stopped"));
  }

  async drain(): Promise<void> { await Promise.all(this.queues.values()); }

  /** Stopping the agent means letting the turns it already accepted finish. */
  async stop(): Promise<void> {
    await this.drain();
    this.started = false;
  }
}
