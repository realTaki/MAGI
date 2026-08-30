import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const APP_ROOT = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: "ui",
  publicDir: "public",
  plugins: [react(), tailwindcss()],
  server: {
    // Operator UI origin. py-magi no longer serves this page.
    port: 42069,
    // Development serves only the UI. The full Webapp process owns /api and
    // /asp, both backed by ~/.magi/app.sqlite.
    allowedHosts: true,
  },
  build: {
    outDir: path.resolve(APP_ROOT, "ui", "dist"),
    emptyOutDir: true,
    sourcemap: true,
  },
});
