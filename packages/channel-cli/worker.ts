import { BaseWorker, MAGI_CONTACT_ID, type Bus, type MessageDeliveryJob } from "@magi/bus";

export class CliWorker extends BaseWorker {
  readonly worker_name = "cli";
  constructor(bus: Bus, private readonly deliver: (text: string, delivery: MessageDeliveryJob) => void = (text) => console.log(text)) { super(bus); }

  async poll(): Promise<boolean> {
    const board = this.bus.board("MessageDeliveryJob");
    // This MAGI's own words, in the chats this channel owns: the message supplies its
    // chat, and the chat supplies the channel, so the job itself is only a message id.
    const job = board.claim(this.worker_name, (input) => {
      const message = this.bus.messages.get(input.message_id);
      return message?.contact_id === MAGI_CONTACT_ID && this.bus.chats.get(message.chat_id)?.channel === this.worker_name;
    });
    if (!job) return false;
    try {
      const message = this.bus.messages.get(job.input.message_id);
      if (!message) throw new Error(`message ${job.input.message_id} does not exist`);
      this.deliver(message.content, job.input);
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }
}
