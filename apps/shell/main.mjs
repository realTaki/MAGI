/**
 * Electron shell. It opens the window and starts the eva launcher for the
 * shell role. ASP and other nodes are their own launchers. Electron does not
 * start them.
 */
import { spawn } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import dugite from "dugite";
import { app, BrowserWindow, Menu, clipboard, ipcMain, screen, shell } from "electron";

import { shellArtifactPath } from "./shell-path.mjs";
import { loadApp, reloadInterface } from "./interface.mjs";
import { prependRuntimePath } from "./runtime-environment.mjs";
import { updateScript } from "./update-script.mjs";
import { createSupervisor, prepareAndStartShell } from "./process.mjs";
import { createProcessOwner } from "./process-owner.mjs";
import { ensureInstalledDependencies } from "./installation.mjs";
import { profilePaths, prepareShellProfile } from "./profile-layout.mjs";

const { resolveGitBinary, setupEnvironment } = dugite;
const SHELL_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SHELL_DIR, "..", "..");
const MAGI_REPOSITORY =
  process.env.MAGI_REPOSITORY_URL ?? "https://github.com/AgenticSocietyLab/MAGI.git";

// Keep every MAGI-owned Electron path beneath ~/.magi so removing that one
// directory also removes Chromium storage, caches, logs, and crash dumps.
const osHome = process.env.MAGI_OS_HOME?.trim() || app.getPath("home");
const PROFILE_PATHS = process.env.MAGI_HOME?.trim()
  ? {
      dataRoot: process.env.MAGI_HOME.trim(),
      shellHome: path.join(process.env.MAGI_HOME.trim(), "shell"),
      shell: path.join(process.env.MAGI_HOME.trim(), "shell", "MAGI"),
      cache: path.join(process.env.MAGI_HOME.trim(), "shell", "cache"),
    }
  : profilePaths(osHome);
const MAGI_DATA_ROOT = PROFILE_PATHS.dataRoot;
const MAGI_SHELL_HOME = PROFILE_PATHS.shellHome;
const ELECTRON_USER_DATA = path.join(MAGI_SHELL_HOME, "electron");
const ELECTRON_CACHE = path.join(PROFILE_PATHS.cache, "electron");
const ELECTRON_LOGS = path.join(MAGI_SHELL_HOME, "logs");
const ELECTRON_CRASH_DUMPS = path.join(MAGI_SHELL_HOME, "crash-dumps");
const WINDOW_STATE_FILE = path.join(MAGI_SHELL_HOME, "window-state.json");
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

app.setPath("userData", ELECTRON_USER_DATA);
app.setPath("sessionData", ELECTRON_USER_DATA);
app.setPath("cache", ELECTRON_CACHE);
app.commandLine.appendSwitch("disk-cache-dir", ELECTRON_CACHE);
app.setPath("crashDumps", ELECTRON_CRASH_DUMPS);
app.setAppLogsPath(ELECTRON_LOGS);

let mainWindow = null;
let startingLocal = false;
let runtimeRootPromise = null;
const processOwner = createProcessOwner();
const command = processOwner.command;

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
  if (!existsSync(node) || !existsSync(npm)) {
    throw new Error("MAGI.app is missing its bundled Node.js or npm runtime");
  }

  const git = resolveGitBinary();
  const gitEnvironment = setupEnvironment({}).env;
  const env = {
    ...prependRuntimePath(gitEnvironment, [
      path.dirname(git),
      path.dirname(node),
      path.dirname(npm),
      path.join(runtime, "bin"),
    ]),
    NODE: node,
    npm_node_execpath: node,
    npm_config_cache: path.join(PROFILE_PATHS.cache, "npm"),
    ELECTRON_CACHE: path.join(PROFILE_PATHS.cache, "electron-downloads"),
    ELECTRON_BUILDER_CACHE: path.join(PROFILE_PATHS.cache, "electron-builder"),
    XDG_CACHE_HOME: PROFILE_PATHS.cache,
  };
  return { env, git, node, npm };
}


// Electron starts the eva launcher. The launcher starts the shell child.
// Renderer reloads do not kill that launcher.
let localApi = null;
let localApiError = null;
let supervisor = null;
// Where the bundled runtime is, when this build has one. The app starts every
// child process from this: Node.js and npm for the TypeScript components.
let localTools = null;
// Only a checkout this app owns (packaged, or an explicit scratch checkout) may
// be rewired; a developer's own working tree must stay untouched.
let localCheckoutManaged = false;

// Unpackaged runs use the source build's MAGI-owned runtime.
function devTools() {
  return toolsForRuntime(path.join(SHELL_DIR, "runtime"));
}

function shellProcessSpec(appCheckout) {
  const root = app.isPackaged || localCheckoutManaged ? appCheckout : path.join(SHELL_DIR, "..", "..");
  return {
    entry: path.join(root, "apps", "eva", "eva.mjs"),
    cwd: path.join(root, "apps", "eva"),
    args: ["--role", "shell"],
  };
}

async function spawnShell(runtimeRoot, appCheckout) {
  const tools = localTools ?? devTools();
  processOwner.assertOpen();
  supervisor ??= createSupervisor({
    node: tools.node, env: tools.env, owner: processOwner,
    onHostRestart: (handle) => { localApi = handle; },
  });
  const spec = shellProcessSpec(appCheckout);
  const ownedDirectory = (directory) => {
    if (typeof directory !== "string") throw new Error("Command requires an owned source directory");
    const actual = realpathSync(directory);
    const roots = [runtimeRoot, appCheckout, MAGI_DATA_ROOT].map((root) => realpathSync(root));
    const normalized = (value) => process.platform === "win32" ? value.toLowerCase() : value;
    if (!roots.some((root) => normalized(actual) === normalized(root) || normalized(actual).startsWith(`${normalized(root)}${path.sep}`))) throw new Error("Command directory is outside Shell-owned source trees");
    return actual;
  };
  return await prepareAndStartShell(
    {
      ...spec,
      env: {
        ...tools.env,
        MAGI_CHECKOUT: runtimeRoot,
        MAGI_APP_CHECKOUT: appCheckout,
        MAGI_OS_HOME: app.getPath("home"),
        MAGI_HOME: process.env.MAGI_HOME?.trim() || path.join(app.getPath("home"), ".magi"),
        MAGI_HOST: "1",
        MAGI_USER_DATA: app.getPath("userData"),
        MAGI_REPOSITORY,
        MAGI_MANAGED: localCheckoutManaged ? "1" : "",
        MAGI_PACKAGED: app.isPackaged ? "1" : "",
        MAGI_SHELL_VERSION: app.getVersion(),
        MAGI_GIT: tools.git,
        MAGI_NODE: tools.node,
        MAGI_NPM: tools.npm,
      },
    },
    { ...tools, managed: localCheckoutManaged, command },
    supervisor,
    {
      host: {
        command: ({ file, args, options }) => {
          processOwner.assertOpen();
          if (![tools.node, tools.git].includes(file) || !Array.isArray(args) || !args.every((arg) => typeof arg === "string")) throw new Error("Unsupported owned command");
          return command(file, args, { ...options, cwd: ownedDirectory(options?.cwd) });
        },
        prepareDependencies: ({ root }) => {
          processOwner.assertOpen();
          if (!localCheckoutManaged) throw new Error("This checkout is not managed by Shell");
          return ensureInstalledDependencies(ownedDirectory(root), { ...tools, command, assertOpen: processOwner.assertOpen });
        },
        openExternal: ({ url }) => shell.openExternal(url),
        copy: ({ text }) => clipboard.writeText(text),
        installShellUpdate: ({ installer }) => installShellUpdate(installer),
        emit: ({ event, payload }) => sendToWindow(mainWindow, "local:event", { event, payload }),
        progress: ({ message, percent }) => reportStartup(mainWindow, message, percent),
      },
    },
  );
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
      const checkout = await prepareShellProfile({
        home: app.getPath("home"), repository: MAGI_REPOSITORY, tools: localTools,
        command, assertOpen: processOwner.assertOpen, progress,
      });
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
  sendToWindow(win, "startup:progress", { message, percent });
}

async function launchLocalOperator(win) {
  if (startingLocal || processOwner.closing) {
    return;
  }
  startingLocal = true;
  try {
    const progress = (message, percent) => reportStartup(win, message, percent);
    progress("Checking local MAGI…", 0.02);
    const runtimeRoot = await resolveRuntimeRoot(progress);
    const appCheckout = runtimeRoot;
    progress("Loading the app…", 0.15);
    if (localApi === null) {
      try {
        localApi = await spawnShell(runtimeRoot, appCheckout);
        localApiError = null;
      } catch (error) {
        localApiError = error instanceof Error ? error : new Error(String(error));
        console.error(`[magi-app] could not start Shell: ${localApiError.message}`);
        throw localApiError;
      }
    }
    const { ui } = await localApi.invoke("prepare");
    await localApi.invoke("start");
    if (!win.isDestroyed()) {
      await loadApp(win, ui);
    }
  } catch (error) {
    if (processOwner.closing) return;
    if (error instanceof Error && error.message === "Shell process exited") {
      localApi = null;
    }
    if (!win.isDestroyed()) {
      win.webContents.send(
        "startup:error",
        error instanceof Error ? error.message : "Could not start the local app.",
      );
    }
  } finally {
    startingLocal = false;
  }
}

async function loadStartup(win) {
  await win.loadFile(path.join(SHELL_DIR, "boot", "index.html"));
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
  if (typeof method !== "string") {
    throw new Error(`Unknown app method: ${String(method)}`);
  }
  return await localApi.invoke(method, payload);
});

ipcMain.handle("startup:retry", async () => {
  if (mainWindow !== null) {
    await loadStartup(mainWindow);
    void launchLocalOperator(mainWindow);
  }
});

ipcMain.handle("shell:reload-interface", async () => {
  processOwner.assertOpen();
  return reloadInterface(localApi, mainWindow);
});

ipcMain.handle("shell:copy-text", (_event, text) => {
  if (typeof text !== "string") {
    throw new TypeError("Clipboard content must be a string.");
  }
  clipboard.writeText(text);
});

async function installShellUpdate(installer) {
  if (!app.isPackaged) {
    throw new Error("A development shell cannot replace itself from a Release.");
  }
  if (typeof installer !== "string" || installer === "" || !existsSync(installer) || !statSync(installer).isFile()) {
    throw new Error("The app did not provide a downloaded installer file.");
  }
  armShellSwap(installer);
  setImmediate(() => app.quit());
  return { quitting: true };
}

function focusMainWindow() {
  if (mainWindow === null || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

const ownsInstance = app.requestSingleInstanceLock();
if (!ownsInstance) {
  app.quit();
} else {
  app.on("second-instance", () => {
    focusMainWindow();
  });

  // Relocate profile cache before any BrowserWindow can open Chromium files
  // inside it (Windows refuses renaming directories with those files open).
  const profilePreparation = app.isPackaged
    ? resolveRuntimeRoot(() => {}).then(() => null, (error) => error)
    : Promise.resolve(null);
  app.whenReady().then(async () => {
    await profilePreparation;
    if (processOwner.closing) return;
    Menu.setApplicationMenu(null);
    mainWindow = createWindow();
    void loadStartup(mainWindow).then(() => launchLocalOperator(mainWindow));
    app.on("activate", () => {
      if (processOwner.closing) return;
      if (BrowserWindow.getAllWindows().length === 0) {
        mainWindow = createWindow();
        void loadStartup(mainWindow).then(() => launchLocalOperator(mainWindow));
      } else {
        focusMainWindow();
      }
    });
  });
}

let quitAllowed = false, quitPromise = null;
app.on("before-quit", (event) => {
  if (quitAllowed) return;
  event.preventDefault();
  // Close admission synchronously, before a finishing prepare can spawn Shell.
  processOwner.beginClose(); supervisor?.beginClose();
  quitPromise ??= (async () => {
    try { await (supervisor ? supervisor.close() : processOwner.close()); }
    catch (error) { console.error("Shell shutdown cleanup failed:", error); }
    finally { localApi = null; quitAllowed = true; app.quit(); }
  })();
});

function armShellSwap(installer) {
  const mount = path.join(app.getPath("temp"), "magi-shell-mount");
  const appBundle = shellArtifactPath({
    platform: process.platform, execPath: process.execPath, appImage: process.env.APPIMAGE,
  });
  const scriptPath = path.join(
    app.getPath("temp"),
    process.platform === "win32" ? "magi-shell-update.cmd" : "magi-shell-update.sh",
  );
  writeFileSync(
    scriptPath,
    updateScript({
      platform: process.platform,
      pid: process.pid,
      installer,
      mount,
      appBundle,
      stagedApp: path.join(mount, "MAGI.app"),
      nextBundle: path.join(app.getPath("temp"), "MAGI.app.next"),
    }),
  );
  if (process.platform !== "win32") {
    chmodSync(scriptPath, 0o755);
  }
  const command = process.platform === "win32" ? "cmd.exe" : "/bin/sh";
  const args = process.platform === "win32" ? ["/c", scriptPath] : [scriptPath];
  spawn(command, args, { detached: true, stdio: "ignore", windowsHide: true }).unref();
}

app.on("window-all-closed", () => {
  // MAGI is a foreground desktop runtime: closing its last window is an
  // application exit on every platform. `before-quit` then stops the ASP and
  // every MAGI process owned by the desktop backend. Keeping the usual macOS
  // background-app behaviour here would leave those children running after
  // the user has closed MAGI's only window.
  app.quit();
});
