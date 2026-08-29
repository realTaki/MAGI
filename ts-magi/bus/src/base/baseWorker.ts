import type { Slot } from "./job.js";
import { BusForWorker } from "../busForWorker.js";

export abstract class BaseWorker {
  abstract readonly workerName: string;
  abstract readonly requiredSlots: readonly Slot[];

  #bus: BusForWorker | null = null;
  #stop: AbortController | null = null;
  #running: Promise<void> | null = null;

  get isAttached(): boolean {
    return this.#bus !== null;
  }

  protected get bus(): BusForWorker {
    if (!this.#bus) throw new Error(`${this.workerName} is not attached`);
    return this.#bus;
  }

  async attach(bus: BusForWorker): Promise<void> {
    if (this.#bus) throw new Error(`${this.workerName} is already attached`);
    this.#bus = bus;
    this.#stop = new AbortController();
    try {
      await this.onAttached();
      this.#running = this.run(this.#stop.signal);
    } catch (error) {
      bus.detach();
      this.#bus = null;
      this.#stop = null;
      throw error;
    }
  }

  async detach(): Promise<void> {
    if (!this.#bus) return;
    this.#stop?.abort();
    await this.#running;
    this.#bus.detach();
    this.#bus = null;
    this.#stop = null;
    this.#running = null;
  }

  protected async onAttached(): Promise<void> {}

  protected run(signal: AbortSignal): Promise<void> {
    if (signal.aborted) return Promise.resolve();
    return new Promise((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
  }
}
