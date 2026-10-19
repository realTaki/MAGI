import { BaseWorker, MAGI_CONTACT_ID, messageDelivery, type Bus } from "@magi/bus";
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
  constructor(bus: Bus, private readonly options: PeerOptions = {}) { super(bus); this.client = new PeerClient(bus.identity); }
  get origin(): string | null { return this.server?.origin ?? null; }
  async start(): Promise<void> {
    const savedPort = this.bus.settings.get("peer.port");
    const port = this.options.port ?? (savedPort ? Number(savedPort) : 0);
    if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error("invalid peer port");
    const server = new PeerServer(this.bus.identity, this.bus.handle, (principal, message) => {
      return messageDelivery.receivePeer(this.bus, {
        principal, endpoint: message.reachableURL, conversationUUID: message.conversationUUID,
        messageId: message.messageId, content: message.content, replyTo: message.replyTo,
      });
    }, {
      host: this.options.host ?? this.bus.settings.get("peer.host") ?? "127.0.0.1", port,
      reachableURL: this.options.reachableURL ?? this.bus.settings.get("peer.publicURL") ?? undefined,
    });
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
      return message?.contact_id === MAGI_CONTACT_ID && this.bus.chats.get(message.chat_id)?.channel === "peer" && (this.bus.deliveries.get(input.message_id)?.next_attempt_at ?? 0) <= Date.now();
    });
    if (!job) return false;
    try {
      const message = this.bus.messages.get(job.input.message_id);
      const chat = message && this.bus.chats.get(message.chat_id);
      if (!message || !chat?.peer_id || !chat.conversation_uuid) throw new PeerError("peer delivery has no conversation");
      const contact = this.bus.contacts.list().find(contact => contact.peer_id === chat.peer_id);
      // A hello records a pending sender. Only the local agent's explicit addcontact
      // decision can accept its route. Merely claiming @user/@shell never grants it.
      if (!contact || contact.peer_trust !== "accepted" || !contact.peer_url || !contact.peer_principal) throw new PeerError("peer contact is not accepted", 403);
      const metadata = job.input.metadata?.peer as { messageId?: string; replyTo?: string } | undefined;
      if (job.input.metadata && Object.keys(job.input.metadata).some(key => key !== "peer")) throw new PeerError("peer transport accepts ordinary chat only");
      const payload: PeerMessage = {
        messageId: metadata?.messageId ?? `${this.client.principal}:${message.id}`,
        conversationUUID: chat.conversation_uuid, content: message.content,
        reachableURL: this.server.origin, handle: this.bus.handle,
        ...(metadata?.replyTo ? { replyTo: metadata.replyTo } : {}),
      };
      await this.client.send(contact.peer_url, contact.peer_principal, payload);
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      const messageId = job.input.message_id;
      // Permanent protocol/auth failures are visible, never retried blindly.
      if (error instanceof PeerError && error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429) {
        board.submit(this.worker_name, job.id, { error: error.message });
      } else {
        const retry = this.bus.deliveries.retry(messageId, error instanceof Error ? error.message : String(error));
        if (retry.exhausted) board.submit(this.worker_name, job.id, { error: "peer delivery exhausted retries; inspect the contact endpoint and retry explicitly" });
        else board.release(this.worker_name, job.id);
      }
    }
    return true;
  }
}
