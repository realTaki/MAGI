/** Webapp's own local REST API, backed by the shared local database. */

function respond(response, status, value) {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(value));
}

async function body(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 1_000_000) return null;
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { return undefined; }
}

function route(pathname) {
  return pathname.replace(/^\/api(?=\/|$)/, "").split("/").filter(Boolean).map(decodeURIComponent);
}

/** Create routes for browser configuration and locally cached UI state. */
export function createAppApi({ store }) {
  return {
    async handle(request, response, url) {
      try {
        const parts = route(url.pathname);
        if (request.method === "GET" && parts.length === 1 && parts[0] === "health") return respond(response, 200, { status: "ok" });
        if (parts[0] === "settings") {
          if (request.method === "GET" && parts.length === 1) return respond(response, 200, { settings: store.listSettings() });
          if (parts.length === 2) {
            if (request.method === "GET") {
              const value = store.getSetting(parts[1]);
              return value === undefined ? respond(response, 404, { error: { code: "setting.not_found" } }) : respond(response, 200, { key: parts[1], value });
            }
            if (request.method === "PUT") {
              const input = await body(request);
              if (!input || !("value" in input)) return respond(response, 400, { error: { code: "request.invalid", detail: "body must contain value" } });
              store.setSetting(parts[1], input.value);
              return respond(response, 200, { key: parts[1], value: input.value });
            }
          }
        }
        if (request.method === "GET" && parts.length === 3 && parts[0] === "magis" && parts[2] === "conversations") return respond(response, 200, { conversations: store.listConversations(parts[1]) });
        if (request.method === "PUT" && parts.length === 2 && parts[0] === "conversations") {
          const input = await body(request);
          if (!input || typeof input.magiId !== "string" || !input.magiId) return respond(response, 400, { error: { code: "request.invalid", detail: "body must contain magiId" } });
          store.saveConversation({ ...input, id: parts[1] });
          return respond(response, 200, { id: parts[1] });
        }
        return respond(response, 404, { error: { code: "route.not_found" } });
      } catch (error) {
        console.error(error);
        return respond(response, 500, { error: { code: "internal", detail: "internal app API error" } });
      }
    },
  };
}
