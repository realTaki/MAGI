import { BaseWorker, MAGI_CONTACT_ID, type Bus, type MessageDeliveryJob } from "@magi/bus";

export class CliWorker extends BaseWorker {
  readonly worker_name = "cli";
  constructor(bus: Bus, private readonly deliver: (text: string, delivery: MessageDeliveryJob) => void = (text) => console.log(text)) { super(bus); }

  async poll(): Promise<boolean> {
    const board = this.bus.board("MessageDeliveryJob");
    // This MAGI's own words, in the chats this channel owns: which channel a chat is on
    // is a fact about the chat, so the job carries the chat and nothing else.
    const job = board.claim(this.worker_name, (input) =>
      input.contact_id === MAGI_CONTACT_ID && this.bus.chats.get(input.chat_id)?.channel === this.worker_name);
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
