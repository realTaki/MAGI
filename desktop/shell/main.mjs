/**
 * Electron shell. It prepares the environment — bundled tools, the checkout,
 * ASP — and opens a window. The app in desktop/app is the interface and the
 * local backend; add a capability there, not in this file.
 */
import { spawn } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import dugite from "dugite";
import { app, BrowserWindow, Menu, clipboard, ipcMain, screen, shell } from "electron";

import { resolveStartupEntry } from "./startup-entry.mjs";

const { resolveGitBinary, setupEnvironment } = dugite;
const SHELL_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SHELL_DIR, "..", "..");
const MAGI_REPOSITORY =
  process.env.MAGI_REPOSITORY_URL ?? "https://github.com/AgenticSocietyLab/MAGI.git";

// Keep every MAGI-owned Electron path beneath ~/.magi so removing that one
// directory also removes Chromium storage, caches, logs, and crash dumps.
// Capture the former default first so existing theme/locale and legacy account
// metadata can be migrated after an upgrade.
const LEGACY_ELECTRON_USER_DATA = app.getPath("userData");
const MAGI_DATA_ROOT = path.join(app.getPath("home"), ".magi");
const MAGI_CHECKOUT_ROOT = path.join(MAGI_DATA_ROOT, "MAGI");
const MAGI_APP_DATA = path.join(MAGI_DATA_ROOT, "app");
const ELECTRON_USER_DATA = path.join(MAGI_APP_DATA, "electron");
const ELECTRON_CACHE = path.join(MAGI_DATA_ROOT, "cache", "electron");
const ELECTRON_LOGS = path.join(MAGI_APP_DATA, "logs");
const ELECTRON_CRASH_DUMPS = path.join(MAGI_APP_DATA, "crash-dumps");
const WINDOW_STATE_FILE = path.join(MAGI_APP_DATA, "window-state.json");
const DEFAULT_WINDOW_SIZE = { width: 1040, height: 760 };
const MINIMUM_WINDOW_SIZE = { width: 720, height: 560 };
const WINDOW_EDGE_MARGIN = 24;

for (const directory of [
  ELECTRON_USER_DATA,
  ELECTRON_CACHE,
  ELECTRON_LOGS,
  ELECTRON_CRASH_DUMPS,
]) {
  mkdirSync(directory, { recursive: true });
}

// Theme and locale are the only localStorage values the interface owns. Move
// their LevelDB directory before Chromium opens the new profile.
const legacyLocalStorage = path.join(LEGACY_ELECTRON_USER_DATA, "Local Storage");
const localStorage = path.join(ELECTRON_USER_DATA, "Local Storage");
if (
  LEGACY_ELECTRON_USER_DATA !== ELECTRON_USER_DATA &&
  existsSync(legacyLocalStorage) &&
  !existsSync(localStorage)
) {
  try {
    renameSync(legacyLocalStorage, localStorage);
  } catch {
    cpSync(legacyLocalStorage, localStorage, { recursive: true });
    rmSync(legacyLocalStorage, { recursive: true, force: true });
  }
}

app.setPath("userData", ELECTRON_USER_DATA);
app.setPath("sessionData", ELECTRON_USER_DATA);
app.setPath("cache", ELECTRON_CACHE);
app.setPath("crashDumps", ELECTRON_CRASH_DUMPS);
app.setAppLogsPath(ELECTRON_LOGS);

let mainWindow = null;
let startingLocal = false;
let runtimeRootPromise = null;

function command(command, args, { cwd, env, description }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let output = "";
    const collect = (chunk) => {
      output = `${output}${String(chunk)}`.slice(-64_000);
    };
    child.stdout?.on("data", collect);
    child.stderr?.on("data", collect);
    child.once("error", (error) => reject(new Error(`${description}: ${error.message}`)));
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve(output);
      } else {
        reject(
          new Error(
            `${description} (${code ?? signal ?? "unknown"})${output.trim() ? `: ${output.trim()}` : ""}`,
          ),
        );
      }
    });
  });
}

function sendToWindow(win, channel, payload) {
  if (win !== null && win !== undefined && !win.isDestroyed()) {
    win.webContents.send(channel, payload);
  }
}

function packagedTools() {
  return toolsForRuntime(path.join(process.resourcesPath, "runtime"));
}

function toolsForRuntime(runtime) {
  const node = path.join(runtime, "bin", process.platform === "win32" ? "node.exe" : "node");
  const npm = path.join(runtime, "npm", "node_modules", "npm", "bin", "npm-cli.js");
  const bun = path.join(runtime, "bin", process.platform === "win32" ? "bun.exe" : "bun");
  if (!existsSync(node) || !existsSync(npm) || !existsSync(bun)) {
    throw new Error("MAGI.app is missing its bundled Node.js, npm, or Bun runtime");
  }

  const git = resolveGitBinary();
  const gitEnvironment = setupEnvironment({}).env;
  const env = {
    ...gitEnvironment,
    PATH: [
      path.dirname(git),
      path.dirname(node),
      path.dirname(npm),
      path.join(runtime, "bin"),
      gitEnvironment.PATH ?? "",
    ].join(path.delimiter),
    NODE: node,
    npm_node_execpath: node,
    npm_config_cache: path.join(MAGI_DATA_ROOT, "cache", "npm"),
  };
  return { env, git, node, npm, bun };
}

async function cloneMagiSource(tools, progress) {
  const destination = MAGI_CHECKOUT_ROOT;
  if (existsSync(destination)) {
    if (!statSync(destination).isDirectory()) {
      throw new Error(`MAGI source path is not a directory: ${destination}`);
    }
    if (!existsSync(path.join(destination, ".git"))) {
      throw new Error(`MAGI source path is not a Git repository: ${destination}`);
    }
    return destination;
  }

  progress("Cloning MAGI source…", 0.06);
  const parent = path.dirname(destination);
  mkdirSync(parent, { recursive: true });
  const staging = mkdtempSync(path.join(parent, ".MAGI-"));
  const checkout = path.join(staging, "MAGI");
  try {
    await command(tools.git, ["clone", MAGI_REPOSITORY, checkout], {
      cwd: staging,
      env: tools.env,
      description: "Could not clone MAGI from GitHub",
    });
    try {
      renameSync(checkout, destination);
    } catch (error) {
      // Two app launches can race on first install. Keep the winner's tree.
      if (!existsSync(destination)) {
        throw error;
      }
    }
    return destination;
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
}

// The shell loads the app and forwards calls. It does not implement them.
// Packaged, the backend is the checkout's, so an edit there applies on the
// next launch. Unpackaged, it is the app next to this shell — a scratch
// checkout (MAGI_DEV_CHECKOUT) is only the Git tree, not the code under test.
let localApi = null;
let localApiError = null;
// Cache-busted app backends are reloadable, but an older instance may still
// own the ASP child it started. Retain each instance until desktop shutdown so
// the stable shell, rather than a client reload, owns final process cleanup.
const localApiInstances = new Set();
// Where the bundled runtime is, when this build has one. The app starts every
// child process from this: npm and Bun for the TypeScript components.
let localTools = null;
// Only a checkout this app owns (packaged, or an explicit scratch checkout) may
// be rewired; a developer's own working tree must stay untouched.
let localCheckoutManaged = false;

// Unpackaged runs use the source build's MAGI-owned runtime.
function devTools() {
  return toolsForRuntime(path.join(SHELL_DIR, "..", "runtime"));
}

function appBackendEntry(runtimeRoot) {
  if (app.isPackaged) {
    return path.join(runtimeRoot, "desktop", "app", "main", "index.mjs");
  }
  return path.join(SHELL_DIR, "..", "app", "main", "index.mjs");
}

async function loadLocalApp(runtimeRoot) {
  const entry = appBackendEntry(runtimeRoot);
  localApi = null;
  localApiError = null;
  if (!existsSync(entry)) {
    localApiError = new Error(`The app backend is missing: ${entry}`);
    console.error(`[magi-app] ${localApiError.message}`);
    return;
  }
  try {
    // Cache-bust on mtime so a retry in this process picks up an edited backend.
    const backend = await import(`${pathToFileURL(entry).href}?v=${statSync(entry).mtimeMs}`);
    localApi = backend.createLocalApi({
      paths: {
        checkout: runtimeRoot,
        home: app.getPath("home"),
        userData: app.getPath("userData"),
        legacyUserData: LEGACY_ELECTRON_USER_DATA,
      },
      repository: MAGI_REPOSITORY,
      managed: localCheckoutManaged,
      tools: localTools ?? devTools(),
      emit: (event, payload) => sendToWindow(mainWindow, "local:event", { event, payload }),
      openExternal: (url) => shell.openExternal(url),
      copy: (text) => clipboard.writeText(text),
    });
    localApiInstances.add(localApi);
  } catch (error) {
    localApiError = error instanceof Error ? error : new Error(String(error));
    console.error(`[magi-app] could not load the local backend: ${localApiError.message}`);
  }
}


async function resolveRuntimeRoot(progress) {
  if (!app.isPackaged) {
    const override = process.env.MAGI_DEV_CHECKOUT ?? "";
    if (override === "") {
      // The developer's own working tree: the app runs from it, but no local
      // capability may rewire it.
      localCheckoutManaged = false;
      return REPO_ROOT;
    }
    // A scratch checkout keeps local capabilities testable with the source
    // build's runtime.
    const checkout = path.resolve(override);
    if (!existsSync(path.join(checkout, ".git"))) {
      throw new Error(`MAGI_DEV_CHECKOUT is not a Git checkout: ${checkout}`);
    }
    progress(`Using the checkout at ${checkout}`, 0.12);
    localCheckoutManaged = true;
    return checkout;
  }
  if (runtimeRootPromise === null) {
    runtimeRootPromise = (async () => {
      localTools = packagedTools();
      const checkout = await cloneMagiSource(localTools, progress);
      localCheckoutManaged = true;
      return checkout;
    })().catch((error) => {
      runtimeRootPromise = null;
      throw error;
    });
  }
  return runtimeRootPromise;
}

function reportStartup(win, message, percent) {
  sendToWindow(win, "asp:startup-progress", { message, percent });
}

async function launchLocalOperator(win) {
  if (startingLocal) {
    return;
  }
  startingLocal = true;
  try {
    const progress = (message, percent) => reportStartup(win, message, percent);
    progress("Checking local MAGI…", 0.02);
    const runtimeRoot = await resolveRuntimeRoot(progress);
    progress("Loading the app…", 0.15);
    // A retry replaces only reloadable backend resources. ASP and its MAGI
    // children have an independent lifecycle and must survive client reloads.
    localApi?.dispose?.();
    await loadLocalApp(runtimeRoot);
    if (localApi === null) {
      throw localApiError ?? new Error("The desktop app backend is not loaded.");
    }
    const { ui } = await localApi.prepare(progress);
    await localApi.start(progress);
    if (!win.isDestroyed()) {
      await loadApp(win, ui);
    }
  } catch (error) {
    if (!win.isDestroyed()) {
      win.webContents.send(
        "asp:startup-error",
        error instanceof Error ? error.message : "Could not start local magi-asp.",
      );
    }
  } finally {
    startingLocal = false;
  }
}

function startupEntry() {
  const checkout = app.isPackaged
    ? MAGI_CHECKOUT_ROOT
    : path.resolve(process.env.MAGI_DEV_CHECKOUT || REPO_ROOT);
  return resolveStartupEntry({
    checkout,
    fallback: path.join(SHELL_DIR, "boot", "index.html"),
  });
}

async function loadStartup(win) {
  await win.loadFile(startupEntry());
}

// The app reports its own entry point: a built file or a dev server URL.
async function loadApp(win, ui) {
  if (ui.startsWith("http")) {
    await win.loadURL(ui);
    return;
  }
  await win.loadFile(ui);
}

function savedWindowState() {
  try {
    const state = JSON.parse(readFileSync(WINDOW_STATE_FILE, "utf8"));
    const bounds = state?.bounds;
    if (
      !bounds ||
      ![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite) ||
      bounds.width <= 0 ||
      bounds.height <= 0
    ) {
      return null;
    }
    return {
      bounds: {
        x: Math.round(bounds.x),
        y: Math.round(bounds.y),
        width: Math.round(bounds.width),
        height: Math.round(bounds.height),
      },
      maximized: state.maximized === true,
    };
  } catch {
    return null;
  }
}

function fitBoundsToWorkArea(bounds, workArea) {
  const availableWidth = Math.max(1, workArea.width - WINDOW_EDGE_MARGIN * 2);
  const availableHeight = Math.max(1, workArea.height - WINDOW_EDGE_MARGIN * 2);
  const width = Math.min(Math.max(bounds.width, MINIMUM_WINDOW_SIZE.width), availableWidth);
  const height = Math.min(Math.max(bounds.height, MINIMUM_WINDOW_SIZE.height), availableHeight);
  const minX = workArea.x + WINDOW_EDGE_MARGIN;
  const minY = workArea.y + WINDOW_EDGE_MARGIN;
  const maxX = workArea.x + workArea.width - WINDOW_EDGE_MARGIN - width;
  const maxY = workArea.y + workArea.height - WINDOW_EDGE_MARGIN - height;
  return {
    x: Math.min(Math.max(bounds.x, minX), maxX),
    y: Math.min(Math.max(bounds.y, minY), maxY),
    width,
    height,
  };
}

function initialWindowState() {
  const saved = savedWindowState();
  const display = saved
    ? screen.getDisplayMatching(saved.bounds)
    : screen.getPrimaryDisplay();
  const idealBounds = saved?.bounds ?? {
    x: display.workArea.x + Math.round((display.workArea.width - DEFAULT_WINDOW_SIZE.width) / 2),
    y: display.workArea.y + Math.round((display.workArea.height - DEFAULT_WINDOW_SIZE.height) / 2),
    ...DEFAULT_WINDOW_SIZE,
  };
  return {
    bounds: fitBoundsToWorkArea(idealBounds, display.workArea),
    maximized: saved?.maximized ?? false,
  };
}

function writeWindowState(win) {
  if (win.isDestroyed() || win.isMinimized() || win.isFullScreen()) {
    return;
  }
  const temporary = `${WINDOW_STATE_FILE}.tmp`;
  try {
    writeFileSync(
      temporary,
      `${JSON.stringify({
        bounds: win.getNormalBounds(),
        maximized: win.isMaximized(),
      })}\n`,
    );
    renameSync(temporary, WINDOW_STATE_FILE);
  } catch {
    rmSync(temporary, { force: true });
  }
}

function createWindow() {
  const initial = initialWindowState();
  const win = new BrowserWindow({
    ...initial.bounds,
    minWidth: Math.min(MINIMUM_WINDOW_SIZE.width, initial.bounds.width),
    minHeight: Math.min(MINIMUM_WINDOW_SIZE.height, initial.bounds.height),
    title: "MAGI",
    show: false,
    ...(process.platform === "darwin"
      ? { titleBarStyle: "hiddenInset" }
      : { frame: false }),
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
  win.once("ready-to-show", () => {
    if (initial.maximized) {
      win.maximize();
    }
    win.show();
  });
  let saveWindowStateTimer = null;
  const scheduleWindowStateSave = () => {
    clearTimeout(saveWindowStateTimer);
    saveWindowStateTimer = setTimeout(() => writeWindowState(win), 250);
  };
  win.on("move", scheduleWindowStateSave);
  win.on("resize", scheduleWindowStateSave);
  win.on("close", () => {
    clearTimeout(saveWindowStateTimer);
    writeWindowState(win);
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  win.on("closed", () => {
    clearTimeout(saveWindowStateTimer);
    if (mainWindow === win) {
      mainWindow = null;
    }
  });
  return win;
}

// One generic bridge: every local capability lives in the app backend, so the
// shell does not change when the app grows one.
ipcMain.handle("local:invoke", async (_event, method, payload) => {
  if (localApi === null) {
    throw localApiError ?? new Error("The desktop app backend is not loaded yet.");
  }
  if (typeof method !== "string" || !Object.hasOwn(localApi, method)) {
    throw new Error(`Unknown app method: ${String(method)}`);
  }
  return await localApi[method](payload);
});

ipcMain.handle("asp:retry", async () => {
  if (mainWindow !== null) {
    await loadStartup(mainWindow);
    void launchLocalOperator(mainWindow);
  }
});

ipcMain.handle("shell:copy-text", (_event, text) => {
  if (typeof text !== "string") {
    throw new TypeError("Clipboard content must be a string.");
  }
  clipboard.writeText(text);
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  mainWindow = createWindow();
  void loadStartup(mainWindow).then(() => launchLocalOperator(mainWindow));
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow();
      void loadStartup(mainWindow).then(() => launchLocalOperator(mainWindow));
    }
  });
});

app.on("before-quit", () => {
  // The desktop owns the processes started by every cache-busted backend
  // instance. Only a real application quit, never a client reload, stops them.
  for (const api of localApiInstances) {
    api.shutdown?.();
  }
  localApiInstances.clear();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
