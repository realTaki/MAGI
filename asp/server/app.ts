/** Compose the ASP operator and participant protocol APIs. */

import type { IncomingMessage } from "node:http";
import type { WebSocket } from "ws";

import { LocalDatabase, defaultDatabasePath } from "../db/database.ts";
import { bearerHandle, HttpError, listen, optionalInt, optionalString, requireContent, requireObject, Router, type RunningServer } from "./http.ts";
import { loadOrCreateOperator } from "./operator.ts";
import { OperatorService } from "./operator-service.ts";
import { Conflict, NotAllowed, NotFound, SessionService } from "./service.ts";
import { defaultSpawner, type MagiSpawner } from "./spawn.ts";
import { type AgentSeed, Store } from "./store.ts";
import { ConnectionError, TimeoutError, Transport } from "./transport.ts";

const PROVIDER_SETTING_KEY = "provider";

export type AspSeed = Record<string, string | AgentSeed>;

export type CreateAppOptions = {
  databasePath?: string;
  aspSeed?: AspSeed;
  magiSpawner?: MagiSpawner;
  aspBase?: string;
  requestShutdown?: () => void;
};

type InitialMessage = {
  content: unknown;
  metadata?: unknown;
};

export class AspApp {
  readonly database: LocalDatabase;
  readonly store: Store;
  readonly transport: Transport;
  readonly sessions: SessionService;
  readonly spawner: MagiSpawner;
  readonly baseUrl: string;
  readonly operator: OperatorService;
  readonly requestShutdown?: () => void;
  origin = "";
  operatorHandle = "user";
  operatorToken = "";
  #running: RunningServer | null = null;
  #restore: NodeJS.Timeout | null = null;
  #managedStopped = false;
  #seed: AspSeed;

  constructor(options: CreateAppOptions = {}) {
    this.database = new LocalDatabase(options.databasePath ?? defaultDatabasePath());
    this.store = new Store(this.database);
    this.transport = new Transport(this.store);
    this.sessions = new SessionService(this.store, this.transport);
    this.spawner = options.magiSpawner ?? defaultSpawner();
    this.baseUrl = options.aspBase ?? intranetBaseUrl();
    this.requestShutdown = options.requestShutdown;
    this.operator = new OperatorService(
      this.sessions,
      this.store,
      this.transport,
      this.spawner,
      this.baseUrl,
    );
    this.#seed = options.aspSeed ?? {};
  }

  async listen(host = "127.0.0.1", port = 0): Promise<void> {
    this.database.open();
    this.store.activate(this.#seed);
    const [handle, token] = loadOrCreateOperator(this.database);
    this.operatorHandle = handle;
    this.operatorToken = token;
    this.store.registerAgent({ handle, token });
    const router = new Router();
    registerRoutes(router, this);
    this.#running = await listen(router, (request, socket) => this.#accept(request, socket), host, port);
    this.origin = this.#running.origin;
    this.#restore = setTimeout(() => {
      if (this.#managedStopped) return;
      for (const agent of this.store.agents.values()) {
        if (agent.managed && !this.transport.isOnline(agent.handle)) {
          this.spawner.spawn({ handle: agent.handle, base: this.baseUrl, token: agent.token });
        }
      }
    }, 5_000);
  }

  async close(): Promise<void> {
    if (this.#restore !== null) {
      clearTimeout(this.#restore);
      this.#restore = null;
    }
    this.spawner.close();
    await this.transport.close();
    if (this.#running !== null) {
      await this.#running.close();
      this.#running = null;
    }
    this.database.close();
  }

  async stopManagedMagis(): Promise<void> {
    this.#managedStopped = true;
    if (this.#restore !== null) clearTimeout(this.#restore);
    this.#restore = null;
    this.spawner.close();
    for (let attempt = 0; attempt < 50; attempt++) {
      const online = [...this.store.agents.values()].some(
        (agent) => agent.managed && this.transport.isOnline(agent.handle),
      );
      if (!online) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new HttpError(409, "Some MAGI remain online; stop their old processes before rebuilding.");
  }

  startManagedMagis(): number {
    this.#managedStopped = false;
    let started = 0;
    for (const agent of this.store.agents.values()) {
      if (agent.managed && !this.transport.isOnline(agent.handle)) {
        if (this.spawner.spawn({ handle: agent.handle, base: this.baseUrl, token: agent.token }).spawned) started++;
      }
    }
    return started;
  }

  async #accept(request: IncomingMessage, socket: WebSocket): Promise<void> {
    const header = request.headers.authorization;
    const value = Array.isArray(header) ? header[0] ?? "" : header ?? "";
    const token = value.startsWith("Bearer ") ? value.slice("Bearer ".length) : "";
    const agent = token === "" ? undefined : this.store.authenticate(token);
    if (agent === undefined) {
      socket.close(1008);
      return;
    }
    await this.transport.connect(agent.handle, socket);
    socket.on("message", (data) => {
      try {
        const parsed: unknown = parseSocket(data);
        if (!isMessage(parsed)) {
          return;
        }
        if (parsed.type === "session.ack") {
          if (typeof parsed.session_id === "string" && typeof parsed.event_id === "string") {
            try {
              this.sessions.acknowledge(agent.handle, parsed.session_id, [parsed.event_id]);
            } catch (error) {
              if (!(error instanceof NotFound)) {
                throw error;
              }
            }
          }
          return;
        }
        this.transport.receiveControl(agent.handle, parsed);
      } catch {
        socket.close();
      }
    });
    socket.on("close", () => {
      setImmediate(() => {
        void this.transport.disconnect(agent.handle, socket);
      });
    });
    socket.on("error", () => undefined);
  }
}

export function createApp(options: CreateAppOptions = {}): AspApp {
  return new AspApp(options);
}

export function intranetBaseUrl(): string {
  const host = process.env.MAGI_ASP_HOST ?? "127.0.0.1";
  const port = process.env.MAGI_ASP_PORT ?? "42069";
  return process.env.MAGI_ASP_PUBLIC_URL || `http://${host}:${port}`;
}

function registerRoutes(router: Router, app: AspApp): void {
  const auth = (headers: IncomingMessage["headers"]) => bearerHandle(headers, (token) => app.store.authenticate(token));
  const operatorOnly = (headers: IncomingMessage["headers"]) => {
    const handle = auth(headers);
    if (handle !== "user") {
      throw new HttpError(403, "operator only");
    }
    return handle;
  };

  router.add("GET", "/health", () => ({ body: { status: "ok", runtime: "typescript" } }));
  router.add("GET", "/operator", () => ({
    body: { handle: app.operatorHandle, token: app.operatorToken },
  }));
  router.add("POST", "/runtime/magi/stop", async (context) => {
    operatorOnly(context.headers);
    await app.stopManagedMagis();
    return { body: { stopped: true } };
  });
  router.add("POST", "/runtime/magi/start", (context) => {
    operatorOnly(context.headers);
    return { body: { started: app.startManagedMagis() } };
  });
  router.add("POST", "/runtime/asp/stop", (context) => {
    operatorOnly(context.headers);
    if (!app.requestShutdown) throw new HttpError(501, "ASP shutdown is unavailable in this runtime");
    setTimeout(() => app.requestShutdown?.(), 50);
    return { body: { stopping: true } };
  });

  router.add("POST", "/conversations", async (context) => {
    const creator = operatorOnly(context.headers);
    const body = requireObject(context.body);
    if (body.kind !== "bot" && body.kind !== "group") {
      throw new HttpError(422, "kind must be bot or group");
    }
    return { status: 201, body: await app.operator.createConversation(creator, body.kind) };
  });
  router.add("GET", "/conversations", (context) => {
    const caller = operatorOnly(context.headers);
    return { body: { conversations: app.operator.listConversations(caller) } };
  });
  router.add("GET", "/conversations/:conversation_id", (context) => {
    const caller = operatorOnly(context.headers);
    const conversationId = requiredParam(context.params, "conversation_id");
    try {
      return { body: app.operator.conversationView(caller, conversationId) };
    } catch (error) {
      hideSessionError(error);
    }
  });
  router.add("PATCH", "/conversations/:conversation_id", async (context) => {
    const caller = operatorOnly(context.headers);
    const body = requireObject(context.body ?? {});
    const topic = plainText(body, "topic");
    const description = plainText(body, "description");
    if (topic === null && description === null) {
      throw new HttpError(400, "nothing to update");
    }
    try {
      return {
        body: await app.operator.updateConversation(
          caller,
          requiredParam(context.params, "conversation_id"),
          topic,
          description,
        ),
      };
    } catch (error) {
      hideSessionError(error);
    }
  });
  router.add("POST", "/conversations/:conversation_id/members", async (context) => {
    const caller = operatorOnly(context.headers);
    const body = requireObject(context.body);
    if (typeof body.handle !== "string" || body.handle === "") {
      throw new HttpError(422, "handle is required");
    }
    try {
      return {
        body: await app.operator.addConversationMember(
          caller,
          requiredParam(context.params, "conversation_id"),
          body.handle,
        ),
      };
    } catch (error) {
      hideSessionError(error);
    }
  });
  router.add("GET", "/bots", (context) => {
    const caller = operatorOnly(context.headers);
    try {
      return { body: { bots: app.operator.listBots(caller, context.query.get("conversation_id")) } };
    } catch (error) {
      hideSessionError(error);
    }
  });
  router.add("PATCH", "/bots/:handle/nickname", async (context) => {
    operatorOnly(context.headers);
    const handle = requiredParam(context.params, "handle");
    const agent = app.store.getAgent(handle);
    if (agent === undefined || handle === "user") {
      throw new HttpError(404, "MAGI not found");
    }
    const body = requireObject(context.body);
    if (typeof body.nickname !== "string") {
      throw new HttpError(422, "nickname is required");
    }
    const nickname = body.nickname.trim();
    if (nickname === "" || nickname.length > 80 || nickname.includes("\r") || nickname.includes("\n")) {
      throw new HttpError(400, "nickname must be one line and 1–80 characters");
    }
    try {
      const updated = await app.transport.updateNickname(handle, nickname);
      if (!updated) {
        throw new HttpError(502, "MAGI could not update nickname");
      }
    } catch (error) {
      if (error instanceof ConnectionError) {
        throw new HttpError(503, "MAGI is offline");
      }
      if (error instanceof TimeoutError) {
        throw new HttpError(504, "MAGI did not confirm nickname");
      }
      throw error;
    }
    agent.nickname = nickname;
    app.store.updateAgent(agent);
    return { body: { handle, nickname } };
  });
  router.add("GET", "/settings/provider/legacy", (context) => {
    operatorOnly(context.headers);
    return { body: app.database.getSetting(PROVIDER_SETTING_KEY) };
  });
  router.add("DELETE", "/settings/provider/legacy", (context) => {
    operatorOnly(context.headers);
    app.database.deleteSetting(PROVIDER_SETTING_KEY);
    return { body: { ok: true } };
  });
  router.add("PUT", "/settings/provider", async (context) => {
    operatorOnly(context.headers);
    const body = requireObject(context.body ?? {});
    const settings = {
      provider: optionalString(body, "provider"),
      model: optionalString(body, "model"),
      api_key: optionalString(body, "api_key"),
    };
    const handles = providerHandles(body.handles);
    const synced: string[] = [];
    const failed: { handle: string; detail: string }[] = [];
    const targets = handles ?? [...app.store.agents.keys()];
    for (const handle of [...new Set(targets)]) {
      if (handle === "user" || app.store.getAgent(handle) === undefined) {
        continue;
      }
      try {
        if (await app.transport.updateProvider(handle, settings)) {
          synced.push(handle);
        } else {
          failed.push({ handle, detail: "MAGI rejected provider configuration" });
        }
      } catch (error) {
        if (error instanceof ConnectionError) {
          continue;
        }
        if (error instanceof TimeoutError) {
          failed.push({ handle, detail: "MAGI did not confirm" });
          continue;
        }
        throw error;
      }
    }
    return { body: { ...settings, synced, failed } };
  });

  router.add("POST", "/sessions", async (context) => {
    const creator = auth(context.headers);
    const body = requireObject(context.body ?? {});
    const initial = initialMessage(body.initial_message);
    const endAfterSend = body.end_after_send === true;
    if (endAfterSend && initial === null) {
      throw new HttpError(400, "end_after_send requires initial_message");
    }
    const result = await app.sessions.createSession({
      creator,
      invite: stringList(body.invite),
      topic: optionalString(body, "topic"),
      initialMessage: initial,
      endAfterSend,
      idempotencyKey: optionalPlainString(body, "idempotency_key"),
    });
    const response: Record<string, unknown> = { session_id: result.sessionId };
    if (result.sequence !== null) {
      response.sequence = result.sequence;
    }
    return { status: 201, body: response };
  });
  router.add("POST", "/sessions/:session_id/join", async (context) => {
    try {
      await app.sessions.join(auth(context.headers), requiredParam(context.params, "session_id"));
    } catch (error) {
      hideSessionError(error);
    }
    return { body: { ok: true } };
  });
  router.add("POST", "/sessions/:session_id/invite", async (context) => {
    const body = requireObject(context.body);
    try {
      const invited = await app.sessions.invite(
        auth(context.headers),
        requiredParam(context.params, "session_id"),
        stringList(body.invite, true),
      );
      return { body: { invited } };
    } catch (error) {
      hideSessionError(error);
    }
  });
  router.add("POST", "/sessions/:session_id/messages", async (context) => {
    const body = requireObject(context.body);
    if (!("content" in body)) {
      throw new HttpError(422, "content is required");
    }
    requireContent(body.content);
    try {
      const result = await app.sessions.sendMessage(
        auth(context.headers),
        requiredParam(context.params, "session_id"),
        body.content,
        optionalPlainString(body, "idempotency_key"),
        "metadata" in body && body.metadata !== null ? body.metadata : null,
      );
      return { status: 201, body: { message_id: result.messageId, sequence: result.sequence } };
    } catch (error) {
      hideSessionError(error);
    }
  });
  router.add("POST", "/sessions/:session_id/leave", async (context) => {
    try {
      await app.sessions.leave(auth(context.headers), requiredParam(context.params, "session_id"));
    } catch (error) {
      hideSessionError(error);
    }
    return { body: { ok: true } };
  });
  router.add("POST", "/sessions/:session_id/end", async (context) => {
    try {
      await app.sessions.end(auth(context.headers), requiredParam(context.params, "session_id"));
    } catch (error) {
      hideSessionError(error);
    }
    return { body: { ok: true } };
  });
  router.add("POST", "/sessions/:session_id/reopen", async (context) => {
    const body = requireObject(context.body ?? {});
    try {
      await app.sessions.reopen(
        auth(context.headers),
        requiredParam(context.params, "session_id"),
        "invite" in body ? stringList(body.invite, true) : null,
        initialMessage(body.initial_message),
      );
    } catch (error) {
      hideSessionError(error);
    }
    return { body: { ok: true } };
  });
  router.add("GET", "/sessions/:session_id", (context) => {
    try {
      return {
        body: app.sessions.getSessionView(auth(context.headers), requiredParam(context.params, "session_id")),
      };
    } catch (error) {
      hideSessionError(error);
    }
  });
  router.add("GET", "/sessions/:session_id/events", (context) => {
    try {
      return {
        body: {
          events: app.sessions.getEventsFor(
            auth(context.headers),
            requiredParam(context.params, "session_id"),
            optionalInt(context.query, "after_sequence"),
            optionalInt(context.query, "limit"),
          ),
        },
      };
    } catch (error) {
      hideSessionError(error);
    }
  });
  router.add("POST", "/sessions/:session_id/events/ack", (context) => {
    const body = requireObject(context.body);
    if (!Array.isArray(body.event_ids) || body.event_ids.length > 500) {
      throw new HttpError(422, "event_ids must be a list of at most 500 ids");
    }
    const eventIds: string[] = [];
    for (const eventId of body.event_ids) {
      if (typeof eventId !== "string") {
        throw new HttpError(422, "event_ids must be strings");
      }
      eventIds.push(eventId);
    }
    try {
      app.sessions.acknowledge(auth(context.headers), requiredParam(context.params, "session_id"), eventIds);
    } catch (error) {
      hideSessionError(error);
    }
    return { body: { ok: true } };
  });
}

function hideSessionError(error: unknown): never {
  if (error instanceof NotFound || error instanceof NotAllowed) {
    throw new HttpError(404, "not found");
  }
  if (error instanceof Conflict) {
    throw new HttpError(409, error.message);
  }
  throw error;
}

function requiredParam(params: Record<string, string>, name: string): string {
  const value = params[name];
  if (value === undefined) {
    throw new HttpError(404, "not found");
  }
  return value;
}

function stringList(value: unknown, required = false): string[] {
  if (value === undefined || value === null) {
    if (required) {
      throw new HttpError(422, "invite is required");
    }
    return [];
  }
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new HttpError(422, "invite must be a list of handles");
  }
  return value;
}

function plainText(body: Record<string, unknown>, field: string): string | null {
  if (!(field in body) || body[field] === null) {
    return null;
  }
  if (typeof body[field] !== "string") {
    throw new HttpError(422, `${field} must be a string`);
  }
  return body[field];
}

function optionalPlainString(body: Record<string, unknown>, field: string): string | null {
  if (!(field in body) || body[field] === null) {
    return null;
  }
  if (typeof body[field] !== "string") {
    throw new HttpError(422, `${field} must be a string`);
  }
  return body[field];
}

function initialMessage(value: unknown): InitialMessage | null {
  if (value === undefined || value === null) {
    return null;
  }
  const body = requireObject(value);
  if (!("content" in body)) {
    throw new HttpError(422, "content is required");
  }
  requireContent(body.content);
  const message: InitialMessage = { content: body.content };
  if ("metadata" in body && body.metadata !== null) {
    message.metadata = body.metadata;
  }
  return message;
}

function providerHandles(value: unknown): string[] | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new HttpError(422, "handles must be a list of handles");
  }
  return value;
}

function parseSocket(data: unknown): unknown {
  const text = Buffer.isBuffer(data) || data instanceof Uint8Array
    ? Buffer.from(data).toString("utf8")
    : typeof data === "string"
      ? data
      : Array.isArray(data)
        ? Buffer.concat(data.filter((part) => Buffer.isBuffer(part))).toString("utf8")
        : "";
  return JSON.parse(text);
}

function isMessage(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
