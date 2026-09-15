/** Electron is a frameless shell: launch chooser, then the running Webapp. */
import { app, BrowserWindow, Menu, ipcMain, shell } from "electron";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SHELL_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SHELL_DIR, "..", "..");
const WEBAPP_DIR = path.join(REPO_ROOT, "webapp");
const WEBAPP_URL = process.env.MAGI_WEBAPP_URL ?? "http://127.0.0.1:42069";

let mainWindow = null;
// Child process for a locally started magi-asp. Not a delivery cache: ASP
// state stays in that process; APP chat/config is Webapp SQLite; MAGI has
// its own store.
let spawnedAsp = null;
let startingLocal = false;

function healthUrl() {
  return new URL("/health", WEBAPP_URL).href;
}

async function waitForUrl(url, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(400) });
      if (response.ok) {
        return;
      }
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  throw new Error(`magi-asp is unavailable at ${url}`);
}

async function isAspHealthy() {
  try {
    const response = await fetch(healthUrl(), { signal: AbortSignal.timeout(400) });
    return response.ok;
  } catch {
    return false;
  }
}

function resolveWebappPython() {
  const unix = path.join(REPO_ROOT, "py-magi", ".venv", "bin", "python");
  const win = path.join(REPO_ROOT, "py-magi", ".venv", "Scripts", "python.exe");
  if (existsSync(unix)) {
    return unix;
  }
  if (existsSync(win)) {
    return win;
  }
  return process.platform === "win32" ? "python" : "python3";
}

function spawnLocalAsp() {
  const python = resolveWebappPython();
  const origin = new URL(WEBAPP_URL);
  const child = spawn(python, ["main.py"], {
    cwd: WEBAPP_DIR,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      MAGI_WEBAPP_HOST: origin.hostname,
      MAGI_WEBAPP_PORT: origin.port || "42069",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stderr?.on("data", (chunk) => {
    const text = String(chunk).trim();
    if (text) {
      console.error("[magi-asp]", text);
    }
  });
  return child;
}

async function startLocalAsp() {
  if (await isAspHealthy()) {
    return;
  }
  if (!existsSync(path.join(WEBAPP_DIR, "main.py"))) {
    throw new Error(`magi-asp was not found at ${WEBAPP_DIR}`);
  }
  spawnedAsp = spawnLocalAsp();
  try {
    await new Promise((resolve, reject) => {
      spawnedAsp.once("error", reject);
      spawnedAsp.once("spawn", resolve);
    });
    await Promise.race([
      waitForUrl(healthUrl()),
      new Promise((_, reject) => {
        spawnedAsp.once("exit", (code, signal) => {
          reject(new Error(`magi-asp exited (${code ?? signal ?? "unknown"})`));
        });
      }),
    ]);
  } catch (error) {
    if (spawnedAsp && !spawnedAsp.killed) {
      spawnedAsp.kill("SIGTERM");
    }
    spawnedAsp = null;
    throw error;
  }
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 900,
    minHeight: 600,
    title: "MAGI",
    show: false,
    frame: false,
    autoHideMenuBar: true,
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(SHELL_DIR, "preload.cjs"),
    },
  });
  win.setMenuBarVisibility(false);
  win.removeMenu();
  win.once("ready-to-show", () => win.show());
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  win.on("closed", () => {
    if (mainWindow === win) {
      mainWindow = null;
    }
  });
  void win.loadFile(path.join(SHELL_DIR, "ui", "index.html"));
  return win;
}

ipcMain.handle("asp:start-local", async () => {
  if (startingLocal) {
    throw new Error("magi-asp is already starting");
  }
  startingLocal = true;
  try {
    await startLocalAsp();
    if (mainWindow === null) {
      throw new Error("MAGI window is gone");
    }
    await mainWindow.loadURL(WEBAPP_URL);
  } finally {
    startingLocal = false;
  }
});

ipcMain.handle("window:control", (_event, action) => {
  if (mainWindow === null) {
    return;
  }
  if (action === "close") {
    mainWindow.close();
    return;
  }
  if (action === "minimize") {
    mainWindow.minimize();
    return;
  }
  if (action === "fullscreen") {
    mainWindow.setFullScreen(!mainWindow.isFullScreen());
  }
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  mainWindow = createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow();
    }
  });
});

app.on("before-quit", () => {
  if (spawnedAsp && !spawnedAsp.killed) {
    spawnedAsp.kill("SIGTERM");
  }
  spawnedAsp = null;
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
