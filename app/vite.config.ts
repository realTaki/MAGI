import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const APP_ROOT = path.dirname(fileURLToPath(import.meta.url));

// Vite dev proxies /api and /ws to whichever FastAPI instance the
// developer started. ``VITE_BACKEND_URL`` wins when set (used inside
// the dev container to point at the sibling ``adam`` service); when
// running vite on the host against a host-launched ``uv run magi``,
// ``VITE_BACKEND_URL`` is unset and we fall back to
// ``http://127.0.0.1:${MAGI_PORT}``.
//
// MAGI_PORT is the MAGI node API (py-magi). Vite itself listens on
// 42069 so the browser / Electron shell share one UI origin.
const BACKEND_URL =
  process.env.VITE_BACKEND_URL ??
  `http://127.0.0.1:${process.env.MAGI_PORT ?? "42070"}`;
const WS_URL = BACKEND_URL.replace(/^http/, "ws");

export default defineConfig({
  root: "ui",
  publicDir: "public",
  plugins: [react(), tailwindcss()],
  server: {
    // Operator UI origin. py-magi no longer serves this page.
    port: 42069,
    // Local development may proxy through arbitrary host names. Vite's default
    // allowlist would reject those names before the /api proxy reaches FastAPI.
    // Production does not run Vite.
    allowedHosts: true,
    proxy: {
      "/api": {
        target: BACKEND_URL,
        changeOrigin: true,
        // ``changeOrigin: true`` also rewrites the Set-Cookie
        // ``Domain`` to the backend's host (127.0.0.1 inside the
        // dev container). When the user is browsing on a different
        // host (e.g. ``localhost`` from the host machine, or any
        // external address), the browser then refuses to attach
        // the cookie to subsequent requests — /me always 401s, the
        // boot routing keeps sending the user back through the
        // wizard, and login looks broken. Stripping the Domain
        // attribute lets the browser bind the cookie to whatever
        // origin the page was loaded from, which is what we want.
        cookieDomainRewrite: "",
      },
      "/ws": {
        target: WS_URL,
        ws: true,
      },
    },
  },
  build: {
    outDir: path.resolve(APP_ROOT, "dist"),
    emptyOutDir: true,
    sourcemap: true,
  },
});
