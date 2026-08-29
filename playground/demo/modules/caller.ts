import { BaseWorker, CallLLMJob, slot, type Slot } from "../../src/index.js";

/** An Agent-facing module: it can create a request but cannot run a provider. */
export class Caller extends BaseWorker {
  readonly workerName = "caller";
  readonly requiredSlots: readonly Slot[] = [slot(CallLLMJob, "publish")];

  ask(content: string): Promise<number> {
    return this.bus.board(CallLLMJob).publish({
      messages: [{ role: "user", content }],
    });
  }
}
