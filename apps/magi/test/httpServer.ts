import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { once } from "node:events";

export type JsonRequest = { method: string; url: URL; body: Record<string, unknown> };

export async function jsonServer(handler: (request: JsonRequest) => Promise<unknown> | unknown): Promise<{ port: number; close(): Promise<void> }> {
  const server = createServer(async (request: IncomingMessage, response: ServerResponse) => {
    try {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      const raw = Buffer.concat(chunks).toString("utf8");
      const body = raw === "" ? {} : JSON.parse(raw) as Record<string, unknown>;
      const result = await handler({ method: request.method ?? "GET", url: new URL(request.url ?? "/", `http://${request.headers.host ?? "127.0.0.1"}`), body });
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify(result));
    } catch (error) {
      response.writeHead(500, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: error instanceof Error ? error.message : String(error) }));
    }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("test server has no TCP port");
  return { port: address.port, close: async () => { server.close(); await once(server, "close"); } };
}
