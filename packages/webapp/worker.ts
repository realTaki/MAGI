/** The webapp worker: a user-facing webapp on a BUS. */

import { BaseWorker, type Bus } from "@magi/bus";

import { createLocalApi } from "./api.js";

export const USER_HANDLE = "@user.magi";
export { AspProcessWorker, ASP_ORIGIN } from "./asp-process.js";
export type { AspHealth, AspProcessOptions, AspProgress } from "./asp-process.js";

export class WebappWorker extends BaseWorker {
  readonly worker_name = "webapp";
  readonly methods: ReturnType<typeof createLocalApi>;
  private apiClosed = false;
  private shutdownClosed = false;
  private wantsShutdown = false;

  constructor(bus: Bus, context: Parameters<typeof createLocalApi>[0]) {
    super(bus);
    this.methods = createLocalApi({ ...context, bus });
  }

  requestShutdown(): void {
    this.wantsShutdown = true;
  }

  async start(): Promise<void> {
    await this.methods.ready();
  }

  async poll(): Promise<boolean> {
    return false;
  }

  async stop(): Promise<void> {
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
