/** The userend worker: operator backend on a BUS. */

import { BaseWorker, type Bus } from "@magi/bus";

import { createLocalApi } from "./api.js";

export const USER_HANDLE = "@user.magi";

export class UserendWorker extends BaseWorker {
  readonly worker_name = "userend";
  readonly methods: ReturnType<typeof createLocalApi>;
  private apiClosed = false;
  private aspClosed = false;
  private wantsShutdown = false;

  constructor(bus: Bus, context: Parameters<typeof createLocalApi>[0]) {
    super(bus);
    this.methods = createLocalApi(context);
  }

  requestShutdown(): void {
    this.wantsShutdown = true;
  }

  async poll(): Promise<boolean> {
    return false;
  }

  async stop(): Promise<void> {
    if (this.wantsShutdown) {
      if (this.aspClosed) return;
      this.aspClosed = true;
      this.apiClosed = true;
      this.methods.shutdown();
      return;
    }
    if (this.apiClosed) return;
    this.apiClosed = true;
    this.methods.dispose();
  }
}
