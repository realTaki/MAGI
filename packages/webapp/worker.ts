/** The webapp worker: a user-facing webapp on a BUS. */

import { BaseWorker, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, type Bus } from "@magi/bus";

import { createLocalApi } from "./api.js";
import { localEvaManagement } from "./local-evas.js";

export { USER_HANDLE } from "@magi/bus";
export { AspProcessWorker, ASP_ORIGIN } from "./asp-process.js";
export type { AspHealth, AspProcessOptions, AspProgress } from "./asp-process.js";

export class WebappWorker extends BaseWorker {
  readonly worker_name = "webapp";
  readonly methods: ReturnType<typeof createLocalApi>;
  private readonly management: ReturnType<typeof localEvaManagement>;
  private pending = new Set<Promise<void>>();
  private apiClosed = false;
  private shutdownClosed = false;
  private wantsShutdown = false;

  constructor(bus: Bus, context: Parameters<typeof createLocalApi>[0]) {
    super(bus);
    this.methods = createLocalApi({ ...context, bus });
    this.management = this.methods.localManagement;
    bus.tools.registerSource("local-evas", () => this.apiClosed ? [] : this.management);
  }

  requestShutdown(): void {
    this.wantsShutdown = true;
  }

  async start(): Promise<void> {
    await this.methods.ready();
  }

  async poll(): Promise<boolean> {
    const tools = this.bus.board("RunToolJob");
    const call = tools.claim(this.worker_name, input => this.management.some(t => t.name === input.call.name));
    if (call) {
      const task = (async () => {
        try {
          const room = call.input.chat_id === undefined ? null : this.bus.chats.get(call.input.chat_id);
          const trigger = call.input.message_id === undefined ? null : this.bus.messages.get(call.input.message_id);
          if (room?.channel !== "shell-owner" || !trigger || trigger.chat_id !== room.id || trigger.contact_id !== SYSTEM_CONTACT_ID) throw new Error("Local EVA management requires an authenticated owner message");
          const tool = this.management.find(t => t.name === call.input.call.name)!;
          const content = await tool.run(call.input.call.arguments as never);
          tools.submit(this.worker_name, call.id, { output: { content } });
        } catch (error) { tools.submit(this.worker_name, call.id, { error: error instanceof Error ? error.message : String(error) }); }
      })();
      this.pending.add(task);
      void task.finally(() => this.pending.delete(task));
      return true;
    }
    const board = this.bus.board("MessageDeliveryJob");
    const delivery = board.claim(this.worker_name, input => {
      const message = this.bus.messages.get(input.message_id);
      return message?.contact_id === MAGI_CONTACT_ID && this.bus.chats.get(message.chat_id)?.channel === "shell-owner";
    });
    if (!delivery) return false;
    if (delivery.input.metadata) this.bus.settings.set(`webapp.message.${delivery.input.message_id}`, JSON.stringify(delivery.input.metadata));
    board.submit(this.worker_name, delivery.id, { output: {} });
    return true;
  }

  async stop(): Promise<void> {
    await Promise.all(this.pending);
    if (this.wantsShutdown) {
      if (this.shutdownClosed) return;
      this.shutdownClosed = true;
      this.apiClosed = true;
      await this.methods.shutdown();
      return;
    }
    if (this.apiClosed) return;
    this.apiClosed = true;
    await this.methods.dispose();
  }
}
