/** The one local Webapp process: UI, API proxy, ASP and one SQLite database. */
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { join, normalize, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { openAppStore } from "./localdb/app-store.mjs";
import { openAspStore } from "./localdb/asp-store.mjs";
import { openLocalDatabase } from "./localdb/database.mjs";
import { createAspOperator } from "./magi-asp/src/server.mjs";

const ROOT = fileURLToPath(new URL(".", import.meta.url));
const MIME = { ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };

function fileUnder(distDir, pathname) {
  const rel = pathname === "/" ? "index.html" : decodeURIComponent(pathname).replace(/^\//, "");
  const file = normalize(join(distDir, rel));
  if (relative(distDir, file).startsWith(`..${sep}`) || relative(distDir, file) === "..") return null;
  if (existsSync(file) && statSync(file).isFile()) return file;
  const index = join(distDir, "index.html");
  return existsSync(index) ? index : null;
}

function proxy(request, response, backendUrl) {
  const backend = new URL(backendUrl);
  const target = new URL(request.url ?? "/", backend);
  const send = target.protocol === "https:" ? httpsRequest : httpRequest;
  const upstream = send(target, { method: request.method, headers: { ...request.headers, host: backend.host } }, (source) => {
    response.writeHead(source.statusCode ?? 502, source.headers);
    source.pipe(response);
  });
  upstream.on("error", () => {
    if (!response.headersSent) response.writeHead(502, { "content-type": "text/plain" });
    response.end("MAGI backend is unavailable");
  });
  request.pipe(upstream);
}

export async function startWebapp({
  host = "127.0.0.1",
  port = 42069,
  dataDir,
  distDir = join(ROOT, "dist"),
  backendUrl = process.env.MAGI_BACKEND_URL ?? "http://127.0.0.1:42070",
} = {}) {
  const database = await openLocalDatabase({ dataDir });
  const appStore = await openAppStore({ database });
  const aspStore = await openAspStore({ database });
  const asp = createAspOperator({ store: aspStore });
  const server = createServer((request, response) => {
    const url = new URL(request.url ?? "/", `http://${request.headers.host ?? host}`);
    if (url.pathname === "/health") return response.end(JSON.stringify({ status: "ok" }));
    if (url.pathname === "/asp" || url.pathname.startsWith("/asp/")) return void asp.handle(request, response, url);
    if (url.pathname === "/api" || url.pathname.startsWith("/api/")) return proxy(request, response, backendUrl);
    const file = fileUnder(distDir, url.pathname);
    if (!file) { response.writeHead(404); response.end("not found"); return; }
    response.writeHead(200, { "content-type": MIME[file.slice(file.lastIndexOf("."))] ?? "application/octet-stream" });
    createReadStream(file).pipe(response);
  });
  server.on("upgrade", (request, socket, head) => {
    const url = new URL(request.url ?? "/", `http://${request.headers.host ?? host}`);
    if (!asp.upgrade(request, socket, head, url)) socket.destroy();
  });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, host, resolve); });
  const address = server.address();
  const boundPort = typeof address === "object" && address ? address.port : port;
  return {
    url: `http://${host}:${boundPort}`,
    databasePath: database.databasePath,
    appStore,
    aspStore,
    close: async () => {
      asp.close();
      await new Promise((resolve) => server.close(resolve));
      appStore.close();
      aspStore.close();
      database.close();
    },
  };
}
