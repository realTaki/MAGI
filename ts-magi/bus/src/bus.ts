import { join } from "node:path";

import { type AnyJobType, type JobBoard, type Slot } from "./base/job.js";
import { Sqlite } from "./base/sqlite.js";
import { BusForWorker } from "./busForWorker.js";
import { createFirmware, type Firmware } from "./firmware/index.js";

export class Bus {
  readonly #owners = new Map<string, string>();

  private constructor(
    private readonly sqlite: Sqlite,
    private readonly firmware: Firmware,
  ) {}

  static async open(workspace: string): Promise<Bus> {
    const sqlite = await Sqlite.open(join(workspace, "memories", "magi.db"));
    return new Bus(sqlite, createFirmware(sqlite));
  }

  forWorker(workerId: string, slots: readonly Slot[]): BusForWorker | null {
    if (!workerId.trim() || slots.length === 0) return null;
    for (const item of slots) {
      const board = this.firmware.boards.get(item.job);
      const owner = this.#owners.get(this.#slotKey(item));
      if (!board?.operations.has(item.operation) || (owner !== undefined && owner !== workerId)) {
        return null;
      }
    }
    for (const item of slots) this.#owners.set(this.#slotKey(item), workerId);
    return new BusForWorker(
      workerId,
      slots,
      (job) => this.#board(job),
      async (namespace, values) => this.firmware.boostDefaultSettings(namespace, values),
      () => this.#release(workerId),
    );
  }

  close(): void {
    this.sqlite.close();
  }

  #board(job: string): JobBoard<AnyJobType> {
    const board = this.firmware.boards.get(job);
    if (!board) throw new Error(`unknown JobBoard: ${job}`);
    return board;
  }

  #release(workerId: string): void {
    for (const [key, owner] of this.#owners) {
      if (owner === workerId) this.#owners.delete(key);
    }
  }

  #slotKey(slot: Slot): string {
    return `${slot.job}:${slot.operation}`;
  }
}
