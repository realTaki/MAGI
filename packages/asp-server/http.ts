/** Small JSON router. ASP's HTTP surface is the route table in app.ts. */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { WebSocketServer, type WebSocket } from "ws";

export class HttpError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

export type RequestContext = {
  method: string;
  path: string;
  query: URLSearchParams;
  headers: IncomingMessage["headers"];
  body: unknown;
  params: Record<string, string>;
};

type RouteHandler = (context: RequestContext) => Promise<RouteResult> | RouteResult;
type RouteResult = { status?: number; body?: unknown } | undefined;

type Route = {
  method: string;
  pattern: string;
  handler: RouteHandler;
};

const LOCAL_ORIGIN = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/;

export class Router {
  readonly #routes: Route[] = [];

  add(method: string, pattern: string, handler: RouteHandler): void {
    this.#routes.push({ method, pattern, handler });
  }

  async handle(context: Omit<RequestContext, "params">): Promise<RouteResult | null> {
    for (const route of this.#routes) {
      if (route.method !== context.method) {
        continue;
      }
      const params = matchPath(route.pattern, context.path);
      if (params === null) {
        continue;
      }
      return await route.handler({ ...context, params });
    }
    return null;
  }
}

export type UpgradeHandler = (request: IncomingMessage, socket: WebSocket) => Promise<void>;

export type RunningServer = {
  origin: string;
  close: () => Promise<void>;
};

export async function listen(
  router: Router,
  onUpgrade: UpgradeHandler,
  host: string,
  port: number,
): Promise<RunningServer> {
  const sockets = new WebSocketServer({ noServer: true });
  const server = createServer((request, response) => {
    void respond(router, request, response);
  });
  server.on("upgrade", (request, socket, head) => {
    const url = requestUrl(request);
    if (url.pathname !== "/connect") {
      socket.destroy();
      return;
    }
    sockets.handleUpgrade(request, socket, head, (ws) => {
      void onUpgrade(request, ws).catch(() => {
        ws.close();
      });
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => {
      server.off("error", reject);
      resolve();
    });
  });
  const address = server.address();
  const bound = address !== null && typeof address === "object" ? address.port : port;
  return {
    origin: `http://${host === "0.0.0.0" ? "127.0.0.1" : host}:${bound}`,
    close: () => closeServer(server, sockets),
  };
}

function matchPath(pattern: string, actualPath: string): Record<string, string> | null {
  const expected = pattern.split("/").filter((part) => part !== "");
  const actual = actualPath.split("/").filter((part) => part !== "");
  if (expected.length !== actual.length) {
    return null;
  }
  const params: Record<string, string> = {};
  for (let index = 0; index < expected.length; index += 1) {
    const token = expected[index] ?? "";
    const value = actual[index] ?? "";
    if (token.startsWith(":")) {
      params[token.slice(1)] = decodeURIComponent(value);
    } else if (token !== value) {
      return null;
    }
  }
  return params;
}

function requestUrl(request: IncomingMessage): URL {
  return new URL(request.url ?? "/", "http://127.0.0.1");
}

async function readBody(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(bodyChunk(chunk));
  }
  const raw = Buffer.concat(chunks).toString("utf8");
  if (raw.trim() === "") {
    return undefined;
  }
  try {
    return parseJson(raw);
  } catch {
    throw new HttpError(422, "invalid JSON");
  }
}

function bodyChunk(chunk: unknown): Buffer {
  if (Buffer.isBuffer(chunk)) {
    return chunk;
  }
  if (typeof chunk === "string") {
    return Buffer.from(chunk);
  }
  if (chunk instanceof Uint8Array) {
    return Buffer.from(chunk);
  }
  throw new HttpError(400, "unreadable body");
}

function parseJson(raw: string): unknown {
  return JSON.parse(raw);
}

function corsHeaders(request: IncomingMessage): Record<string, string> {
  const origin = request.headers.origin;
  if (typeof origin !== "string" || (origin !== "null" && !LOCAL_ORIGIN.test(origin))) {
    return {};
  }
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-credentials": "true",
    "access-control-allow-methods": "*",
    "access-control-allow-headers": "*",
    vary: "Origin",
  };
}

async function respond(router: Router, request: IncomingMessage, response: ServerResponse): Promise<void> {
  const cors = corsHeaders(request);
  if (request.method === "OPTIONS") {
    response.writeHead(204, cors);
    response.end();
    return;
  }
  try {
    const url = requestUrl(request);
    const result = await router.handle({
      method: request.method ?? "GET",
      path: url.pathname,
      query: url.searchParams,
      headers: request.headers,
      body: await readBody(request),
    });
    if (result === null) {
      sendJson(response, 404, { detail: "not found" }, cors);
      return;
    }
    sendJson(response, result?.status ?? 200, result?.body ?? null, cors);
  } catch (error) {
    if (error instanceof HttpError) {
      sendJson(response, error.status, { detail: error.message }, cors);
      return;
    }
    console.error("[asp]", error instanceof Error ? error.message : String(error));
    sendJson(response, 500, { detail: "internal error" }, cors);
  }
}

function sendJson(
  response: ServerResponse,
  status: number,
  body: unknown,
  extra: Record<string, string>,
): void {
  const payload = JSON.stringify(body);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(payload),
    ...extra,
  });
  response.end(payload);
}

function closeServer(server: Server, sockets: WebSocketServer): Promise<void> {
  for (const client of sockets.clients) {
    client.close();
  }
  sockets.close();
  server.closeAllConnections();
  return new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function requireObject(body: unknown): Record<string, unknown> {
  if (!isRecord(body)) {
    throw new HttpError(422, "expected a JSON object");
  }
  return body;
}

export function optionalString(body: Record<string, unknown>, field: string): string | null {
  if (!(field in body) || body[field] === null) {
    return null;
  }
  if (typeof body[field] !== "string") {
    throw new HttpError(422, `${field} must be a string`);
  }
  const trimmed = body[field].trim();
  return trimmed === "" ? null : trimmed;
}

export function requireContent(value: unknown): void {
  if (typeof value === "string") {
    if (value.length === 0) {
      throw new HttpError(422, "content must not be empty");
    }
    return;
  }
  if (!Array.isArray(value)) {
    return;
  }
  if (value.length === 0) {
    throw new HttpError(422, "content must contain at least one part");
  }
  for (const part of value) {
    if (
      isRecord(part) &&
      part.type === "text" &&
      typeof part.text === "string" &&
      part.text.length === 0
    ) {
      throw new HttpError(422, "text part must not be empty");
    }
  }
}

export function optionalInt(query: URLSearchParams, name: string): number | null {
  const raw = query.get(name);
  if (raw === null || raw === "") {
    return null;
  }
  if (!/^-?\d+$/.test(raw)) {
    throw new HttpError(422, `${name} must be an integer`);
  }
  return Number(raw);
}

export function bearerHandle(
  headers: IncomingMessage["headers"],
  authenticate: (token: string) => { handle: string } | undefined,
): string {
  const header = headers.authorization;
  const value = Array.isArray(header) ? header[0] ?? "" : header ?? "";
  if (!value.startsWith("Bearer ")) {
    throw new HttpError(401, "missing credentials");
  }
  const agent = authenticate(value.slice("Bearer ".length));
  if (agent === undefined) {
    throw new HttpError(401, "invalid credentials");
  }
  return agent.handle;
}
