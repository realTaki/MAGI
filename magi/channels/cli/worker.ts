import { BaseWorker, type Bus, type DeliveryNotify } from "../../bus/index.js";

export class CliWorker extends BaseWorker {
  readonly worker_name = "cli";
  constructor(bus: Bus, private readonly deliver: (text: string, delivery: DeliveryNotify) => void = (text) => console.log(text)) { super(bus); }

  async poll(): Promise<boolean> {
    const board = this.bus.board("DeliveryNotify");
    const job = board.claim(this.worker_name, (input) => input.channel === "cli");
    if (!job) return false;
    try {
      this.deliver(job.input.text, job.input);
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }
}
