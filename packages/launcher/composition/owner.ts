/** Local parent-owned stdio adapter. Never registered on the peer HTTP server. */
import { BaseWorker, chat, messageDelivery, MAGI_CONTACT_ID, type Bus, type ChatInput, richMetadata, reactionsFor, reactToMessage, readMessageAttachment, visibleMessage } from "@magi/bus";

export function createOwnerApi(bus: Bus) {
  const room = bus.chats.forChannel("local-owner", "owner");
  messageDelivery.setHomeChat(bus, room.id);
  return {
    "owner.chat": (input: ChatInput) => chat(bus, room.id, bus.userContactId, input, "local-owner"),
    "owner.react": (input: { message_id: number | string; emoji: string; remove?: boolean }) => reactToMessage(bus, room.id, String(bus.userContactId), input.message_id, input.emoji, input.remove),
    "owner.attachment": (input: { message_id: number | string; attachment_id: string }) => readMessageAttachment(bus, room.id, input.message_id, input.attachment_id),
    "owner.profile": () => ({ handle: bus.handle, owner: true, public_key: bus.identity.publicKey,
      provider: bus.settings.get("provider.name") ?? "", model: bus.settings.get("provider.model") ?? "" }),
    "owner.events": () => bus.messages.list(room.id, 500).filter(message => visibleMessage(bus, message.id)).map(message => ({ id: message.id, content: message.content, ...richMetadata(bus, message.id), reactions: reactionsFor(bus, room.id, message.id, String(bus.userContactId)),
      role: message.contact_id === MAGI_CONTACT_ID ? "assistant" : "user", created_at: message.created_at,
      metadata: JSON.parse(bus.settings.get(`owner.message.${message.id}`) ?? "null") ?? undefined })),
  };
}

export class LocalOwnerWorker extends BaseWorker {
  readonly worker_name = "local-owner";
  async poll(): Promise<boolean> {
    const board = this.bus.board("MessageDeliveryJob");
    const job = board.claim(this.worker_name, input => {
      const message = this.bus.messages.get(input.message_id);
      return message?.contact_id === MAGI_CONTACT_ID && this.bus.chats.get(message.chat_id)?.channel === "local-owner";
    });
    if (!job) return false;
    if (job.input.metadata) this.bus.settings.set(`owner.message.${job.input.message_id}`, JSON.stringify(job.input.metadata));
    board.submit(this.worker_name, job.id, { output: {} });
    return true;
  }
}
