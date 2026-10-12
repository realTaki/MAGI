/** The userend worker: operator backend on a BUS. */

import { BaseWorker, type Bus } from "@magi/bus";

import { createLocalApi } from "./api.js";

export const USER_HANDLE = "@user.magi";

export class UserendWorker extends BaseWorker {
  readonly worker_name = "userend";
  readonly methods: ReturnType<typeof createLocalApi>;

  constructor(bus: Bus, context: Parameters<typeof createLocalApi>[0]) {
    super(bus);
    this.methods = createLocalApi(context);
  }

  async poll(): Promise<boolean> {
    return false;
  }

  async stop(): Promise<void> {
    this.methods.dispose();
  }
}
