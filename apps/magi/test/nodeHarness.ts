import { createServer } from "node:http";
import type { Socket } from "node:net";
import { WebSocketServer, type RawData, type WebSocket } from "ws";

let nextPort = 46000 + Math.floor(Math.random() * 1000);

type ServerSocket = WebSocket;
type UpgradeHost = { upgrade(request: Request): boolean };
type ServeOptions = {
  port?: number;
  fetch(request: Request, host: UpgradeHost): Response | Promise<Response | undefined> | undefined;
  websocket?: { open?(socket: ServerSocket): void; message?(socket: ServerSocket, message: RawData): void };
};

/** A compact Node HTTP + ws fixture for channel integration tests. */
export const nodeHarness = {
  serve(options: ServeOptions): { port: number; stop(force?: boolean): void } {
    const port = options.port && options.port !== 0 ? options.port : nextPort++;
    const sockets = new Set<ServerSocket>();
    const connections = new Set<Socket>();
    const webSocket = new WebSocketServer({ noServer: true });
    const server = createServer(async (request, response) => {
      const origin = `http://${request.headers.host ?? "127.0.0.1"}`;
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      const body = Buffer.concat(chunks);
      const input = new Request(new URL(request.url ?? "/", origin), { method: request.method, body: body.length ? body : undefined });
      const result = await options.fetch(input, { upgrade: () => false });
      if (!result) { response.writeHead(400); response.end("upgrade failed"); return; }
      response.writeHead(result.status, Object.fromEntries(result.headers));
      response.end(Buffer.from(await result.arrayBuffer()));
    });
    server.on("connection", (connection) => {
      connections.add(connection);
      connection.once("close", () => connections.delete(connection));
    });
    server.on("upgrade", async (request, socket, head) => {
      const origin = `http://${request.headers.host ?? "127.0.0.1"}`;
      let upgrade = false;
      await options.fetch(new Request(new URL(request.url ?? "/", origin), { method: request.method }), { upgrade: () => (upgrade = true) });
      if (!upgrade) { socket.destroy(); return; }
      webSocket.handleUpgrade(request, socket, head, (client) => webSocket.emit("connection", client, request));
    });
    webSocket.on("connection", (socket) => {
      sockets.add(socket);
      options.websocket?.open?.(socket);
      socket.on("message", (message) => options.websocket?.message?.(socket, message));
      socket.once("close", () => sockets.delete(socket));
    });
    server.listen(port, "127.0.0.1");
    server.unref();
    return { port, stop() {
      for (const socket of sockets) socket.terminate();
      webSocket.close();
      server.close();
      for (const connection of connections) connection.destroy();
    } };
  },
};
