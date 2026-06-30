import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Vite dev proxies /api and /ws to whichever FastAPI instance the
// developer started. ``VITE_BACKEND_URL`` wins when set (used inside
// the dev container to point at the sibling ``adam`` service); when
// running vite on the host against a host-launched ``uv run magi``,
// ``VITE_BACKEND_URL`` is unset and we fall back to
// ``http://127.0.0.1:${MAGI_PORT}``.
//
// MAGI_PORT is the port the *node* binds; the vite dev server itself
// listens on 42069 so the dev URL matches the prod URL.
const BACKEND_URL =
  process.env.VITE_BACKEND_URL ??
  `http://127.0.0.1:${process.env.MAGI_PORT ?? "42069"}`;
const WS_URL = BACKEND_URL.replace(/^http/, "ws");

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 42069 = the WebUI's port in production (FastAPI bind). Vite dev
    // reuses the same port so the dev URL matches the prod URL.
    port: 42069,
    proxy: {
      "/api": {
        target: BACKEND_URL,
        changeOrigin: true,
      },
      "/ws": {
        target: WS_URL,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});