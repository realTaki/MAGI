/**
 * Electron shell. It prepares what the app needs in order to exist — bundled
 * tools and the checkout — and opens a window. User, ASP, and Eva are the same
 * kind of child: `node <entry> ...args`. Shell owns their lifecycles.
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

import { createAppRebuilder, rebuildRequestKey } from "./rebuild.mjs";
import { shellArtifactPath } from "./shell-path.mjs";
import { prependRuntimePath } from "./runtime-environment.mjs";
import { updateScript } from "./update-script.mjs";
import { createSupervisor, prepareAndStartUser } from "./process.mjs";
import { createProcessOwner } from "./process-owner.mjs";
import { ensureInstalledDependencies } from "./installation.mjs";
import { profilePaths, prepareShellProfile } from "./profile-layout.mjs";
import { ensureManagedClone } from "./clone.mjs";

const { resolveGitBinary, setupEnvironment } = dugite;
const SHELL_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SHELL_DIR, "..", "..");
const MAGI_REPOSITORY =
  process.env.MAGI_REPOSITORY_URL ?? "https://github.com/AgenticSocietyLab/MAGI.git";

// Keep every MAGI-owned Electron path beneath ~/.magi so removing that one
// directory also removes Chromium storage, caches, logs, and crash dumps.
const PROFILE_PATHS = profilePaths(app.getPath("home"));
const MAGI_DATA_ROOT = PROFILE_PATHS.dataRoot;
const MAGI_USER_DATA = path.join(MAGI_DATA_ROOT, "user");
const MAGI_APP_CHECKOUT = path.join(MAGI_USER_DATA, "MAGI");
const ELECTRON_USER_DATA = path.join(MAGI_USER_DATA, "electron");
const ELECTRON_CACHE = path.join(PROFILE_PATHS.cache, "electron");
const ELECTRON_LOGS = path.join(MAGI_USER_DATA, "logs");
const ELECTRON_CRASH_DUMPS = path.join(MAGI_USER_DATA, "crash-dumps");
const WINDOW_STATE_FILE = path.join(MAGI_USER_DATA, "window-state.json");
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


/**
 * The shell needs a User clone before it can spawn `dist/user.js`. All
 * other runtime clones, including ASP, are the User module's responsibility.
 */
async function ensureAppCheckout(checkout, tools, progress) {
  if (!app.isPackaged && !localCheckoutManaged) {
    return checkout;
  }
  await ensureManagedClone({
    run: (args, options) => command(tools.git, args, options),
    env: tools.env,
    url: MAGI_REPOSITORY,
    destination: MAGI_APP_CHECKOUT,
    branch: "magi/user",
  });
  progress("Prepared User source…", 0.14);
  return MAGI_APP_CHECKOUT;
}

// Shell supervises User and forwards calls. Renderer reloads do not kill User.
// The packaged shell runs `dist/user.js` from the User clone. An unpackaged
// developer tree remains untouched, while an explicit scratch checkout uses
// the same isolated-runtime layout as a packaged app.
let localApi = null;
let localApiError = null;
let supervisor = null;
let rebuildFromLink = null;
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

function userProcessSpec(appCheckout) {
  if (app.isPackaged || localCheckoutManaged) {
    return {
      entry: path.join(appCheckout, "apps", "user", "dist", "user.js"),
      cwd: path.join(appCheckout, "apps", "user"),
    };
  }
  return {
    entry: path.join(SHELL_DIR, "..", "user", "dist", "user.js"),
    cwd: path.join(SHELL_DIR, "..", "user"),
  };
}

async function spawnUser(runtimeRoot, appCheckout) {
  const tools = localTools ?? devTools();
  processOwner.assertOpen();
  supervisor ??= createSupervisor({ node: tools.node, env: tools.env, owner: processOwner });
  rebuildFromLink ??= createAppRebuilder({
    supervisor, tools, command, managed: () => localCheckoutManaged,
    pause: () => new Promise((resolve) => setTimeout(resolve, 1000)),
    emit: (payload) => sendToWindow(mainWindow, "local:event", { event: "runtime.rebuild", payload }),
    onUser: async (handle) => {
      localApi = handle;
      const { ui } = await handle.invoke("prepare");
      await handle.invoke("resume");
      sendToWindow(mainWindow, "local:event", { event: "app.interface-updated", payload: { ui } });
    },
  });
  const spec = userProcessSpec(appCheckout);
  const ownedDirectory = (directory) => {
    if (typeof directory !== "string") throw new Error("Command requires an owned source directory");
    const actual = realpathSync(directory);
    const roots = [runtimeRoot, appCheckout, MAGI_DATA_ROOT].map((root) => realpathSync(root));
    const normalized = (value) => process.platform === "win32" ? value.toLowerCase() : value;
    if (!roots.some((root) => normalized(actual) === normalized(root) || normalized(actual).startsWith(`${normalized(root)}${path.sep}`))) throw new Error("Command directory is outside Shell-owned source trees");
    return actual;
  };
  return await prepareAndStartUser(
    {
      ...spec,
      env: {
        ...tools.env,
        MAGI_CHECKOUT: runtimeRoot,
        MAGI_APP_CHECKOUT: appCheckout,
        MAGI_HOME: app.getPath("home"),
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
    const appCheckout = await ensureAppCheckout(runtimeRoot, localTools ?? devTools(), progress);
    progress("Loading the app…", 0.15);
    if (localApi === null) {
      try {
        localApi = await spawnUser(runtimeRoot, appCheckout);
        localApiError = null;
      } catch (error) {
        localApiError = error instanceof Error ? error : new Error(String(error));
        console.error(`[magi-app] could not start User: ${localApiError.message}`);
        throw localApiError;
      }
    }
    const { ui } = await localApi.invoke("prepare");
    await localApi.invoke("start");
    if (!win.isDestroyed()) {
      await loadApp(win, ui);
    }
    appReady = true;
    await deliverDeeplinks();
  } catch (error) {
    if (processOwner.closing) return;
    if (error instanceof Error && error.message === "User process exited") {
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
// shell does not change when the app grows one. A magi:// link is the exception
// the OS forces on us: Shell receives it and owns its lifecycle transaction.
ipcMain.handle("local:invoke", async (_event, method, payload) => {
  if (method === "runtime.rebuildStatus") return rebuildFromLink?.status() ?? [];
  if (method === "runtime.applyDeeplink") {
    if (!rebuildFromLink) throw new Error("Shell lifecycle is not loaded yet.");
    return await rebuildFromLink(payload?.url);
  }
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

const pendingDeeplinks = [];
let deliveringDeeplink = false;
let activeDeeplink = null;
let appReady = false;
let lastDeeplink = { url: "", at: 0 };
let argvDeeplinksQueued = false;

function focusMainWindow() {
  if (mainWindow === null || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function queueDeeplink(raw) {
  if (typeof raw !== "string" || !raw.startsWith("magi:")) return;
  const requestKey = rebuildRequestKey(raw);
  if (pendingDeeplinks.some((url) => rebuildRequestKey(url) === requestKey) || activeDeeplink === requestKey) return;
  const now = Date.now();
  if (lastDeeplink.url === raw && now - lastDeeplink.at < 2000) return;
  lastDeeplink = { url: raw, at: now };
  pendingDeeplinks.push(raw);
  focusMainWindow();
}

function queueArgvDeeplinks() {
  if (argvDeeplinksQueued) return;
  argvDeeplinksQueued = true;
  for (const arg of process.argv) queueDeeplink(arg);
}

async function deliverDeeplinks() {
  if (!appReady || deliveringDeeplink || rebuildFromLink === null) return;
  deliveringDeeplink = true;
  try {
    while (pendingDeeplinks.length > 0) {
      const url = pendingDeeplinks.shift();
      activeDeeplink = rebuildRequestKey(url);
      try {
        await rebuildFromLink(url);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        console.error(`[magi] deeplink: ${message}`);
        sendToWindow(mainWindow, "local:event", {
          event: "runtime.deeplink-failed",
          payload: { url, message },
        });
      }
    }
  } finally {
    activeDeeplink = null;
    deliveringDeeplink = false;
    if (pendingDeeplinks.length > 0) void deliverDeeplinks();
  }
}

function registerMagiProtocol() {
  // An unpackaged `electron .` is not the app the OS registered, so name the
  // executable and the app entry. A packaged build registers `magi://` itself.
  if (process.defaultApp && process.argv.length >= 2) {
    app.setAsDefaultProtocolClient("magi", process.execPath, [path.resolve(process.argv[1])]);
    return;
  }
  app.setAsDefaultProtocolClient("magi");
}

const ownsInstance = app.requestSingleInstanceLock();
if (!ownsInstance) {
  app.quit();
} else {
  app.on("second-instance", (_event, argv) => {
    for (const arg of argv) queueDeeplink(arg);
    if (appReady) void deliverDeeplinks();
  });
  app.on("open-url", (event, url) => {
    event.preventDefault();
    queueDeeplink(url);
    if (appReady) void deliverDeeplinks();
  });
  registerMagiProtocol();
  queueArgvDeeplinks();

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
  // Close admission synchronously, before a finishing prepare can spawn User.
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
