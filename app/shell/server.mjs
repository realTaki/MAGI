/**
 * Production shell: serve ``dist/`` and proxy ``/api`` to a MAGI node.
 *
 * The renderer keeps using same-origin ``/api/...`` fetches (and cookies).
 * Browser/Vite already did this via the Vite proxy; the desktop app does
 * the same with a tiny local HTTP server so ``file://`` is never needed.
 */
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { extname, join, normalize, relative, sep } from "node:path";

const MIME = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".map": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

function safeFile(distDir, urlPath) {
  const decoded = decodeURIComponent((urlPath.split("?")[0] || "/"));
  const rel = decoded === "/" ? "index.html" : decoded.replace(/^\//, "");
  const full = normalize(join(distDir, rel));
  const inside = relative(distDir, full);
  if (inside.startsWith("..") || inside.startsWith(`..${sep}`)) return null;
  if (!existsSync(full) || !statSync(full).isFile()) {
    const index = join(distDir, "index.html");
    return existsSync(index) ? index : null;
  }
  return full;
}

function proxyToBackend(req, res, backendUrl) {
  const backend = new URL(backendUrl);
  const target = new URL(req.url || "/", backend);
  const send = target.protocol === "https:" ? httpsRequest : httpRequest;
  const headers = { ...req.headers, host: backend.host };
  const upstream = send(
    target,
    { method: req.method, headers },
    (up) => {
      res.writeHead(up.statusCode ?? 502, up.headers);
      up.pipe(res);
    },
  );
  upstream.on("error", () => {
    if (!res.headersSent) res.writeHead(502, { "content-type": "text/plain" });
    res.end("MAGI backend is unavailable");
  });
  req.pipe(upstream);
}

export function startShellServer({ distDir, backendUrl, port = 0 }) {
  const server = createServer((req, res) => {
    const path = req.url || "/";
    if (path.startsWith("/api/") || path === "/api" || path.startsWith("/ws")) {
      proxyToBackend(req, res, backendUrl);
      return;
    }
    const file = safeFile(distDir, path);
    if (file == null) {
      res.writeHead(404, { "content-type": "text/plain" });
      res.end("not found");
      return;
    }
    res.writeHead(200, { "content-type": MIME[extname(file)] ?? "application/octet-stream" });
    createReadStream(file).pipe(res);
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => {
      const address = server.address();
      const bound = typeof address === "object" && address ? address.port : port;
      resolve({
        url: `http://127.0.0.1:${bound}`,
        close: () => new Promise((done) => server.close(() => done())),
      });
    });
  });
}
