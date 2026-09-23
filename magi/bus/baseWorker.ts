import type { Bus } from "./bus.js";

export abstract class BaseWorker {
  abstract readonly worker_name: string;
  constructor(protected readonly bus: Bus) {}
  abstract poll(): Promise<boolean>;
}
