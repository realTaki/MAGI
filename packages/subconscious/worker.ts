import { BaseWorker, MAGI_CONTACT_ID, type Bus } from "@magi/bus";
import { Conversation } from "./conversation.js";
import { seedSubconscious } from "./presets.js";
import { subconsciousTools, SUBCONSCIOUS_PROMPT, SUBCONSCIOUS_COMPACTION_PROMPT } from "./tools.js";

/** Owns the private continuous conversation; never consumes an external inbox. */
export class SubconsciousWorker extends BaseWorker {
  readonly worker_name = "subconscious";
  private started = false;
  private active?: Promise<void>;
  private controller?: AbortController;
  constructor(bus: Bus) {
    super(bus);
    bus.prompts.register("subconscious/AGENT", SUBCONSCIOUS_PROMPT);
    bus.prompts.register("subconscious/compaction", SUBCONSCIOUS_COMPACTION_PROMPT);
    bus.tools.registerSource("subconscious", () => this.started ? subconsciousTools(bus) : []);
  }
  async start(): Promise<void> { seedSubconscious(this.bus); this.started = true; }
  async poll(): Promise<boolean> {
    if (!this.started) return false;
    const own = subconsciousTools(this.bus);
    const tools = this.bus.board("RunToolJob");
    const call = tools.claim(this.worker_name, input => own.some(tool => tool.name === input.call.name));
    if (call) {
      try {
        if (call.input.chat_id === undefined || this.bus.chats.get(call.input.chat_id)?.channel !== "subconscious") throw new Error("internal tool requires the subconscious conversation");
        const tool = own.find(tool => tool.name === call.input.call.name)!;
        tools.submit(this.worker_name, call.id, { output: { content: await tool.run(call.input.call.arguments) } });
      } catch (error) { tools.submit(this.worker_name, call.id, { error: error instanceof Error ? error.message : String(error) }); }
      return true;
    }
    if (this.active) return false;
    const board = this.bus.board("MessageDeliveryJob");
    const job = board.claim(this.worker_name, input => {
      const message = this.bus.messages.get(input.message_id);
      return !!message && message.contact_id !== MAGI_CONTACT_ID && this.bus.chats.get(message.chat_id)?.channel === "subconscious";
    });
    if (!job) return false;
    const message = this.bus.messages.get(job.input.message_id)!;
    this.controller = new AbortController();
    const active = new Conversation(this.bus, message.chat_id, this.controller.signal).run(job.id, message.id);
    this.active = active;
    void active.finally(() => { if (this.active === active) { this.active = undefined; this.controller = undefined; } });
    return true;
  }
  interrupt(): void { this.started = false; this.controller?.abort(new Error("MAGI stopped")); }
  async drain(): Promise<void> { await this.active; }
  async stop(): Promise<void> { this.interrupt(); await this.drain(); }
}
