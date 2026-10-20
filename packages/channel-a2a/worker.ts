import { BaseWorker, MAGI_CONTACT_ID, messageDelivery, type Bus, wireAttachments, attachmentText, type RichMessage } from "@magi/bus";
import { GroupReplica } from "./groups.js";
import { PeerClient } from "./client.js";
import { PeerError, type PeerMessage } from "./protocol.js";
import { PeerServer, type PeerServerOptions } from "./server.js";
export type PeerOptions = PeerServerOptions;
export { PeerClient, discoverPeer } from "./client.js";
export { PeerServer } from "./server.js";
export { principalFor, type PeerCard, type PeerMessage, type PeerReceipt } from "./protocol.js";

/** Each EVA owns a listener and its own durable inbox/outbox. No central relay. */
export class PeerWorker extends BaseWorker {
  readonly worker_name = "peer";
  private server: PeerServer | null = null;
  private readonly client: PeerClient;
  readonly groups: GroupReplica;
  constructor(bus: Bus, private readonly options: PeerOptions = {}) { super(bus); this.client = new PeerClient(bus.identity); this.groups = new GroupReplica(bus, this.client); }
  get origin(): string | null { return this.server?.origin ?? null; }
  async start(): Promise<void> {
    const savedPort = this.bus.settings.get("peer.port");
    const port = this.options.port ?? (savedPort ? Number(savedPort) : 0);
    if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error("invalid peer port");
    const server = new PeerServer(this.bus.identity, this.bus.handle, (principal, message) => {
      return messageDelivery.receivePeer(this.bus, {
        principal, endpoint: message.reachableURL, conversationUUID: message.conversationUUID,
        messageId: message.messageId, content: message.content, replyTo: message.replyTo, attachments: message.attachments, reaction: message.reaction, requestReply: message.requestReply,
      });
    }, {
      host: this.options.host ?? this.bus.settings.get("peer.host") ?? "127.0.0.1", port,
      reachableURL: this.options.reachableURL ?? this.bus.settings.get("peer.publicURL") ?? undefined,
    }, { receiveGroup: (principal, wire) => this.groups.receive(principal, wire), sync: (principal, data) => this.groups.respond(principal, data) });
    await server.start();
    this.server = server;
    this.bus.settings.set("peer.reachableURL", server.origin!);
    this.bus.settings.set("peer.principal", server.principal);
    // Stable endpoint on restart, including when the first bind asked for port 0.
    this.bus.settings.set("peer.port", String(server.boundPort));
  }
  async stop(): Promise<void> { const server = this.server; this.server = null; await server?.stop(); }
  health(): string | null { return this.server?.origin ? null : "peer server is not listening"; }
  async poll(): Promise<boolean> {
    if (!this.server?.origin) return false;
    const board = this.bus.board("MessageDeliveryJob");
    const job = board.claim(this.worker_name, input => {
      const message = this.bus.messages.get(input.message_id);
      return message?.contact_id === MAGI_CONTACT_ID && ["peer", "group"].includes(this.bus.chats.get(message.chat_id)?.channel ?? "") && (this.bus.deliveries.get(input.message_id)?.next_attempt_at ?? 0) <= Date.now();
    });
    if (!job) return this.groups.poll();
    try {
      const message = this.bus.messages.get(job.input.message_id);
      const chat = message && this.bus.chats.get(message.chat_id);
      if (chat?.channel === "group") {
        const event = (job.input.metadata?.group as { event?: import("@magi/bus/books/groupBook.js").GroupEventEnvelope } | undefined)?.event;
        if (!event) throw new PeerError("group delivery has no event");
        await this.groups.deliver(event);
        board.submit(this.worker_name, job.id, { output: {} });
        return true;
      }
      if (!message || !chat?.peer_id || !chat.conversation_uuid) throw new PeerError("peer delivery has no conversation");
      const contact = this.bus.contacts.list().find(contact => contact.peer_id === chat.peer_id);
      if (!contact?.peer_url || !contact.peer_principal) throw new PeerError("peer contact has no delivery route");
      const metadata = job.input.metadata?.peer as { messageId?: string; replyTo?: string } | undefined;
      if (job.input.metadata && Object.keys(job.input.metadata).some(key => key !== "peer" && key !== "system_error_source" && key !== "rich")) throw new PeerError("peer transport accepts ordinary chat only");
      const rich = job.input.metadata?.rich as RichMessage | undefined;
      const suffix = rich?.attachments?.length ? `\n\n${attachmentText(rich.attachments)}` : "";
      const content = suffix && message.content.endsWith(suffix) ? message.content.slice(0, -suffix.length) : message.content;
      const payload: PeerMessage = {
        messageId: metadata?.messageId ?? `${this.client.principal}:${message.id}`,
        conversationUUID: chat.conversation_uuid, content,
        reachableURL: this.server.origin, handle: this.bus.handle,
        ...(metadata?.replyTo || rich?.reply_to !== undefined ? { replyTo: metadata?.replyTo ?? String(rich?.reply_to) } : {}),
        ...(rich?.reply_to !== undefined ? { requestReply: true } : {}),
        ...(rich?.attachments?.length ? { attachments: wireAttachments(this.bus.workspace, String(message.chat_id), rich.attachments) } : {}),
        ...(rich?.reaction ? { reaction: rich.reaction } : {}),
      };
      await this.client.send(contact.peer_url, contact.peer_principal, payload);
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      const messageId = job.input.message_id;
      // Permanent protocol failures are visible, never retried blindly.
      if (error instanceof PeerError && error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 409 && error.status !== 429) {
        board.submit(this.worker_name, job.id, { error: error.message });
        messageDelivery.reportError(this.bus, error.message, { module: this.worker_name, source: `delivery:${messageId}`, chat_id: this.bus.messages.get(messageId)?.chat_id, wake: !job.input.metadata?.system_error_source });
      } else {
        const retry = this.bus.deliveries.retry(messageId, error instanceof Error ? error.message : String(error));
        if (retry.exhausted) {
          const text = "peer delivery exhausted retries; inspect the contact endpoint and retry explicitly";
          board.submit(this.worker_name, job.id, { error: text });
          messageDelivery.reportError(this.bus, text, { module: this.worker_name, source: `delivery:${messageId}`, chat_id: this.bus.messages.get(messageId)?.chat_id, wake: !job.input.metadata?.system_error_source });
        }
        else board.release(this.worker_name, job.id);
      }
    }
    return true;
  }
}
