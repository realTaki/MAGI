/** Electron is only a shell for the separately running Webapp. */
import { app, BrowserWindow, shell } from "electron";

const WEBAPP_URL = process.env.MAGI_WEBAPP_URL ?? "http://127.0.0.1:42069";

async function waitForUrl(url, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await fetch(url, { signal: AbortSignal.timeout(400) });
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  throw new Error(`Webapp is unavailable at ${url}. Start it with: npm start --prefix webapp`);
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280, height: 840, minWidth: 900, minHeight: 600, title: "MAGI", show: false,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  });
  win.once("ready-to-show", () => win.show());
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  void win.loadURL(WEBAPP_URL);
}

app.whenReady().then(async () => {
  await waitForUrl(WEBAPP_URL);
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
