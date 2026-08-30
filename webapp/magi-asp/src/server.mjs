/** ASP routes mounted by the one Webapp HTTP server. */
import { WebSocketServer } from "ws";

import { AspError } from "../../localdb/asp-store.mjs";

function bearer(request) {
  const value = request.headers.authorization;
  return value?.startsWith("Bearer ") ? value.slice("Bearer ".length) : undefined;
}

async function body(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 1_000_000) throw new AspError(413, "request.too_large", "JSON body is limited to 1 MB");
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { throw new AspError(400, "request.invalid_json", "body must be JSON"); }
}

function respond(response, status, value) {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(value));
}

function route(pathname) {
  return pathname.split("/").filter(Boolean).map(decodeURIComponent);
}

/** Create the ASP transport; it never opens a port or database itself. */
export function createAspOperator({ store }) {
  const connections = new Map();
  const webSocket = new WebSocketServer({ noServer: true });

  function publish(delivery) {
    for (const { event, recipients } of delivery) {
      const message = JSON.stringify(event);
      for (const handle of recipients) for (const socket of connections.get(handle) ?? []) {
        if (socket.readyState === 1) socket.send(message);
      }
    }
  }

  async function handle(request, response, url) {
    try {
      const pathname = url.pathname.replace(/^\/asp(?=\/|$)/, "") || "/";
      const parts = route(pathname);
      if (request.method === "GET" && pathname === "/health") return respond(response, 200, { status: "ok", protocol: "asp/0.1" });
      if (request.method === "POST" && parts.length === 1 && parts[0] === "agents") return respond(response, 201, store.registerAgent(await body(request)));
      const actor = store.authenticate(bearer(request));
      if (request.method === "PUT" && parts[0] === "agents" && parts[1] === actor && parts[2] === "policy") {
        store.setPolicy(actor, (await body(request)).policy);
        return respond(response, 200, { ok: true });
      }
      if (request.method === "PUT" && parts[0] === "agents" && parts[1] === actor && parts[2] === "allowlist" && parts[3]) {
        store.allow(actor, `@${parts[3].replace(/^@/, "")}`);
        return respond(response, 200, { ok: true });
      }
      if (request.method === "POST" && parts.length === 1 && parts[0] === "sessions") {
        const operation = store.createSession(actor, await body(request));
        publish(operation.delivery);
        return respond(response, 201, operation.result);
      }
      if (parts[0] === "sessions" && parts[1]) {
        const sessionId = parts[1];
        if (request.method === "GET" && parts.length === 2) return respond(response, 200, store.getSession(sessionId, actor));
        if (request.method === "GET" && parts[2] === "events") return respond(response, 200, store.events(sessionId, actor, { afterSequence: url.searchParams.get("after_sequence"), limit: url.searchParams.get("limit") }));
        const action = parts[2];
        const operations = {
          join: () => store.join(sessionId, actor),
          invite: async () => store.invite(sessionId, actor, (await body(request)).invite),
          messages: async () => store.sendMessage(sessionId, actor, await body(request)),
          leave: () => store.leave(sessionId, actor),
          end: () => store.end(sessionId, actor),
        };
        if (request.method === "POST" && operations[action]) {
          const operation = await operations[action]();
          publish(operation.delivery);
          return respond(response, action === "messages" ? 201 : 200, operation.result);
        }
      }
      throw new AspError(404, "route.not_found", "route is unavailable");
    } catch (error) {
      if (error instanceof AspError) return respond(response, error.status, { error: { code: error.code, detail: error.message } });
      console.error(error);
      return respond(response, 500, { error: { code: "internal", detail: "internal operator error" } });
    }
  }

  function upgrade(request, socket, head, url) {
    if (url.pathname !== "/asp/connect") return false;
    let actor;
    try { actor = store.authenticate(bearer(request)); } catch { socket.destroy(); return true; }
    webSocket.handleUpgrade(request, socket, head, (connection) => {
      const peers = connections.get(actor) ?? new Set();
      peers.add(connection);
      connections.set(actor, peers);
      connection.on("close", () => {
        peers.delete(connection);
        if (peers.size === 0) connections.delete(actor);
      });
      for (const event of store.replay(actor)) connection.send(JSON.stringify(event));
    });
    return true;
  }

  return {
    handle,
    upgrade,
    close: () => {
      for (const peers of connections.values()) for (const socket of peers) socket.close();
      webSocket.close();
    },
  };
}
