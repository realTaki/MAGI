import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { decodeEnvelope, encodeEnvelope, MAX_BODY_BYTES, MAX_SYNC_BYTES, record, parseMessage, PEER_PROTOCOL, PeerError, peerURL, principalFor, type PeerCard, type PeerIdentity, type PeerMessage } from "./protocol.js";

import type { GroupWire, SyncResponse } from "./client.js";
export type PeerServerHandlers = { receiveGroup?: (principal: string, group: GroupWire) => { duplicate: boolean }; sync?: (principal: string, data: unknown) => SyncResponse };
export type PeerServerOptions = { host?: string; port?: number; reachableURL?: string };
export class PeerServer {
  private server: Server | null = null;
  private address: string | null = null;
  private port: number | null = null;
  readonly principal: string;
  constructor(private readonly identity: PeerIdentity, private readonly handle: string, private readonly receive: (principal: string, message: PeerMessage) => Promise<{ duplicate: boolean }> | { duplicate: boolean }, private readonly options: PeerServerOptions = {}, private readonly handlers: PeerServerHandlers = {}) {
    this.principal = principalFor(identity.publicKey);
  }
  get origin(): string | null { return this.address; }
  get boundPort(): number | null { return this.port; }
  card(): PeerCard {
    if (!this.address) throw new Error("peer server is not listening");
    return { protocol: PEER_PROTOCOL, principal: this.principal, publicKey: this.identity.publicKey, handle: this.handle, reachableURL: this.address };
  }
  async start(): Promise<void> {
    if (this.server) return;
    const server = createServer((request, response) => { void this.handleRequest(request, response); });
    server.requestTimeout = 10_000;
    server.headersTimeout = 10_000;
    const host = this.options.host ?? "127.0.0.1";
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(this.options.port ?? 0, host, () => { server.off("error", reject); resolve(); });
    });
    const address = server.address();
    if (!address || typeof address === "string") { server.close(); throw new Error("peer did not bind a TCP port"); }
    try { this.address = peerURL(this.options.reachableURL ?? `http://${host.includes(":") ? `[${host}]` : host}:${address.port}`).origin; }
    catch (error) { await new Promise<void>(resolve => server.close(() => resolve())); throw error; }
    this.server = server;
    this.port = address.port;
  }
  async stop(): Promise<void> {
    const server = this.server;
    this.server = null;
    this.address = null;
    this.port = null;
    if (server) await new Promise<void>((resolve, reject) => { server.close(error => error ? reject(error) : resolve()); server.closeAllConnections(); });
  }
  private async handleRequest(request: IncomingMessage, response: ServerResponse): Promise<void> {
    try {
      if (request.method === "GET" && request.url === "/.well-known/magi-peer.json") { this.respond(response, 200, JSON.stringify(this.card())); return; }
      if (request.method !== "POST" || (request.url !== "/chat" && request.url !== "/sync")) throw new PeerError("not found", 404);
      if (request.headers["content-type"]?.split(";")[0].trim() !== "application/json") throw new PeerError("JSON required", 415);
      const chunks: Buffer[] = [];
      let size = 0;
      for await (const chunk of request) {
        size += Buffer.byteLength(chunk);
        if (size > (request.url === "/sync" ? MAX_SYNC_BYTES : MAX_BODY_BYTES)) throw new PeerError("chat is too large", 413);
        chunks.push(Buffer.from(chunk));
      }
      const { principal, data } = decodeEnvelope(Buffer.concat(chunks).toString("utf8"), this.principal, request.url === "/sync" ? "sync" : "chat");
      if (request.url === "/sync") {
        if (!this.handlers.sync) throw new PeerError("group sync unavailable", 404);
        const result = this.handlers.sync(principal, data);
        const body = encodeEnvelope(this.identity, principal, "sync-result", result);
        if (Buffer.byteLength(body) > MAX_SYNC_BYTES) throw new PeerError("sync response is too large", 413);
        this.respond(response, 200, body); return;
      }
      if (record(data) && "group" in data) {
        if (!this.handlers.receiveGroup || Object.keys(data).length !== 1 || !record(data.group) || Object.keys(data.group).length !== 1) throw new PeerError("invalid group chat");
        const group = data as GroupWire;
        const result = this.handlers.receiveGroup(principal, group);
        this.respond(response, 202, encodeEnvelope(this.identity, principal, "receipt", { messageId: group.group.event.payload.eventId, conversationUUID: group.group.event.payload.conversationUUID, accepted: true, duplicate: result.duplicate })); return;
      }
      const message = parseMessage(data);
      // This callback MUST durably record and reconcile the chat before resolving.
      // No fetch of the peer's advertised URL occurs on receipt.
      const result = await this.receive(principal, message);
      this.respond(response, 202, encodeEnvelope(this.identity, principal, "receipt", { messageId: message.messageId, conversationUUID: message.conversationUUID, accepted: true, duplicate: result.duplicate }));
    } catch (error) {
      const status = error instanceof PeerError ? error.status : 503;
      this.respond(response, status, JSON.stringify({ error: error instanceof PeerError ? error.message : "peer could not durably accept chat" }));
    }
  }
  private respond(response: ServerResponse, status: number, body: string): void {
    response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    response.end(body);
  }
}
