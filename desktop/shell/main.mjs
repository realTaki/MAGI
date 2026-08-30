/**
 * MAGI desktop shell. Same React UI as the browser:
 *   - unpackaged: load the Vite dev server (``npm run dev``)
 *   - packaged: serve ``dist/`` and proxy ``/api`` to a MAGI node
 */
import { app, BrowserWindow, shell } from "electron";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { startShellServer } from "./server.mjs";
import { openAppStore } from "../core/app-store.mjs";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const DIST = app.isPackaged
  ? join(process.resourcesPath, "webapp")
  : join(ROOT, "..", "webapp", "dist");
const DEV_URL = process.env.MAGI_APP_DEV_URL ?? "http://127.0.0.1:42069";
const BACKEND_URL = process.env.MAGI_BACKEND_URL ?? "http://127.0.0.1:42070";

let shellClose = null;
let pageUrl = null;
let appStore = null;

async function waitForUrl(url, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await fetch(url, { signal: AbortSignal.timeout(400) });
      return true;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  return false;
}

function createWindow(url) {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 900,
    minHeight: 600,
    title: "MAGI",
    show: false,
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.once("ready-to-show", () => win.show());
  win.webContents.setWindowOpenHandler(({ url: next }) => {
    void shell.openExternal(next);
    return { action: "deny" };
  });
  void win.loadURL(url);
}

async function resolvePageUrl() {
  if (pageUrl) return pageUrl;
  const useDev = !app.isPackaged && process.env.MAGI_ELECTRON_PROD !== "1";
  if (useDev && (await waitForUrl(DEV_URL))) {
    pageUrl = DEV_URL;
    return pageUrl;
  }
  if (!existsSync(join(DIST, "index.html"))) {
    throw new Error(
      useDev
        ? `Vite is not running at ${DEV_URL}. Start it with: npm run dev`
        : `Missing ${DIST}/index.html. Build the UI with: npm run build`,
    );
  }
  const server = await startShellServer({ distDir: DIST, backendUrl: BACKEND_URL });
  shellClose = server.close;
  pageUrl = server.url;
  return pageUrl;
}

app.whenReady().then(async () => {
  // Establish the App-owned store before loading the renderer.  No remote
  // MAGI is contacted here; connection and sync remain explicit actions.
  appStore = await openAppStore();
  createWindow(await resolvePageUrl());
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void resolvePageUrl().then(createWindow);
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  void shellClose?.();
  appStore?.close();
});
