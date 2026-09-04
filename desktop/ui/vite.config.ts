import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const UI_ROOT = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: ".",
  base: "./",
  publicDir: "public",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    allowedHosts: true,
  },
  build: {
    outDir: path.resolve(UI_ROOT, "dist"),
    emptyOutDir: true,
    sourcemap: true,
  },
});
