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

import { prepareDesktopUpdate, acknowledgeDesktopUpdate, desktopUpdateLaunchContext, prepareDesktopUpdateRecovery } from "./desktop-update.mjs";
import { shellArtifactPath } from "./shell-path.mjs";
import { applyInterfaceUpdate, loadApp, reloadInterface } from "./interface.mjs";
import { prependRuntimePath } from "./runtime-environment.mjs";
import { updateScript } from "./update-script.mjs";
import { createSupervisor, prepareAndStartShell } from "./process.mjs";
import { createProcessOwner } from "./process-owner.mjs";
import { ensureInstalledDependencies, assertInstalledDependencies } from "./installation.mjs";
import { profilePaths, prepareShellProfile } from "./profile-layout.mjs";
import { resolveShellCheckout } from "./source-checkout.mjs";

const { resolveGitBinary, setupEnvironment } = dugite;
const SHELL_DIR = path.dirname(fileURLToPath(import.meta.url));
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
  // Activation belongs to Shell alone. Never leak its handoff into ASP/agents.
  for (const key of Object.keys(gitEnvironment)) {
    if (key.startsWith("MAGI_DESKTOP_UPDATE_") || ["MAGI_REVISION", "MAGI_REVISION_DIR", "MAGI_REPLACE_JOB", "MAGI_REPLACE_ERROR"].includes(key)) delete gitEnvironment[key];
  }
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
// Only app-owned installations may automatically install/retarget clones.
// Verified source launches separately allow explicit source sync/build actions.
let localCheckoutManaged = false;
let localSourceVerified = false;

// Unpackaged runs use the source build's MAGI-owned runtime.
function devTools() {
  return toolsForRuntime(path.join(SHELL_DIR, "runtime"));
}

function shellProcessSpec(appCheckout) {
  const root = appCheckout;
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
  const activation = desktopLaunchEnvironment();
  const spec = shellProcessSpec(activation.MAGI_REVISION_DIR || appCheckout);
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
        MAGI_SOURCE_VERIFIED: localSourceVerified ? "1" : "",
        MAGI_PACKAGED: app.isPackaged ? "1" : "",
        MAGI_SHELL_VERSION: app.getVersion(),
        ...activation,
        MAGI_GIT: tools.git,
        MAGI_NODE: tools.node,
        MAGI_NPM: tools.npm,
      },
    },
    { ...tools, managed: localCheckoutManaged && !activation.MAGI_REVISION_DIR, command },
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
          const owned = ownedDirectory(root);
          if (activation.MAGI_REVISION_DIR && owned === realpathSync(activation.MAGI_REVISION_DIR)) return assertInstalledDependencies(owned);
          return ensureInstalledDependencies(owned, { ...tools, command, assertOpen: processOwner.assertOpen });
        },
        openExternal: ({ url }) => shell.openExternal(url),
        copy: ({ text }) => clipboard.writeText(text),
        installDesktopUpdate: ({ manifest, job }) => installLocalDesktopUpdate(manifest, job),
        installShellUpdate: ({ installer }) => installShellUpdate(installer),
        emit: ({ event, payload }) => {
          if (event === "app.interface-updated") {
            void applyInterfaceUpdate(localApi, mainWindow, payload).catch((error) => {
              console.error(`[magi-app] could not reload interface: ${error instanceof Error ? error.message : error}`);
            });
          }
          sendToWindow(mainWindow, "local:event", { event, payload });
        },
        progress: ({ message, percent }) => reportStartup(mainWindow, message, percent),
      },
    },
  );
}


async function resolveRuntimeRoot(progress) {
  if (runtimeRootPromise === null) {
    runtimeRootPromise = (async () => {
      localTools = app.isPackaged ? packagedTools() : devTools();
      const source = await resolveShellCheckout({
        isPackaged: app.isPackaged, shellDirectory: SHELL_DIR,
        override: process.env.MAGI_DEV_CHECKOUT ?? "",
        command, git: localTools.git, env: localTools.env,
        prepareManaged: () => prepareShellProfile({
          home: app.getPath("home"), repository: MAGI_REPOSITORY, tools: localTools,
          command, assertOpen: processOwner.assertOpen, progress,
        }),
      });
      localCheckoutManaged = source.managed;
      localSourceVerified = source.sourceVerified;
      progress(`Using the checkout at ${source.checkout}`, 0.12);
      return source.checkout;
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
      if (app.isPackaged && process.env.MAGI_DESKTOP_UPDATE_JOURNAL && !process.env.MAGI_REPLACE_ERROR) {
        const identity = await localApi.invoke("runtime.interface");
        const packagedRevision = JSON.parse(readFileSync(path.join(SHELL_DIR, "package.json"), "utf8")).magiRevision;
        const context = desktopUpdateLaunchContext({ artifact: process.env.APPIMAGE });
        const expectedUi = path.join(context.sourceDir, "apps", "shell", "dist", "index.html");
        const expectedEntry = path.join(context.sourceDir, "packages", "node", "dist", "child.js");
        if (packagedRevision !== identity.revision || realpathSync(identity.checkout) !== realpathSync(context.sourceDir)
          || realpathSync(identity.entry) !== realpathSync(expectedEntry) || realpathSync(ui) !== realpathSync(expectedUi)
          || realpathSync(identity.ui) !== realpathSync(expectedUi)) throw new Error("Desktop artifact and running Shell/UI revision differ");
        acknowledgeDesktopUpdate({ revision: identity.revision, artifact: process.env.APPIMAGE });
      }
      if (process.env.MAGI_DESKTOP_UPDATE_JOURNAL) {
        supervisor?.consumeDesktopHandoff(process.env.MAGI_DESKTOP_UPDATE_JOURNAL);
        for (const key of Object.keys(process.env)) if (key.startsWith("MAGI_DESKTOP_UPDATE_") || ["MAGI_REVISION", "MAGI_REVISION_DIR", "MAGI_REPLACE_JOB", "MAGI_REPLACE_ERROR"].includes(key)) delete process.env[key];
      }
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
    // Keep native controls on every screen, including startup and failures.
    // Only macOS has inset traffic lights alongside the renderer's drag regions.
    ...(process.platform === "darwin"
      ? { titleBarStyle: "hiddenInset" }
      : { frame: true }),
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

function desktopLaunchEnvironment() {
  if (!app.isPackaged || process.platform !== "linux") return {};
  const context = desktopUpdateLaunchContext({ artifact: process.env.APPIMAGE });
  if (!context) return {};
  return {
    MAGI_DESKTOP_UPDATE_JOURNAL: context.journalPath,
    MAGI_REPLACE_JOB: String(context.job),
    ...(context.error ? { MAGI_REPLACE_ERROR: context.error } : {
      MAGI_REVISION: context.revision, MAGI_REVISION_DIR: context.sourceDir,
    }),
  };
}

let desktopInstallPending = false;
let desktopRecoveryPending = false;
async function recoverInterruptedDesktopUpdate() {
  if (process.platform !== "linux" || !process.env.APPIMAGE || process.env.MAGI_DESKTOP_UPDATE_JOURNAL) return false;
  const target = shellArtifactPath({ platform: process.platform, execPath: process.execPath, appImage: process.env.APPIMAGE });
  const pending = await prepareDesktopUpdateRecovery({ target, nodePath: packagedTools().node });
  if (!pending) return false;
  desktopRecoveryPending = true;
  if (!pending.helperRunning) {
    const helper = spawn(pending.nodePath, [pending.helperPath, "--run", pending.journalPath, String(process.pid)], { detached: true, stdio: "ignore" });
    await new Promise((resolve, reject) => { helper.once("spawn", resolve); helper.once("error", reject); });
    helper.unref();
  }
  setImmediate(() => app.quit());
  return true;
}
async function installLocalDesktopUpdate(manifest, job) {
  processOwner.assertOpen();
  if (!app.isPackaged || process.platform !== "linux") throw new Error("Desktop self-update requires a Linux AppImage");
  if (desktopInstallPending) throw new Error("A desktop update is already pending");
  if (!Number.isInteger(job) || job <= 0 || !manifest || !/^[a-f0-9]{40}$/.test(manifest.revision)) throw new Error("Invalid desktop update request");
  const home = process.env.MAGI_HOME?.trim() || path.join(app.getPath("home"), ".magi");
  const expected = path.join(home, "shell", "revisions", manifest.revision);
  if (realpathSync(manifest.dir) !== realpathSync(expected)) throw new Error("Desktop update is outside Shell-owned revisions");
  desktopInstallPending = true;
  try {
    const target = shellArtifactPath({ platform: process.platform, execPath: process.execPath, appImage: process.env.APPIMAGE });
    const transaction = await prepareDesktopUpdate({ target, execPath: process.execPath, manifest, job, pid: process.pid, home, nodePath: localTools.node });
    const helper = spawn(transaction.nodePath, [transaction.helperPath, "--run", transaction.journalPath], { detached: true, stdio: "ignore", windowsHide: true });
    await new Promise((resolve, reject) => { helper.once("spawn", resolve); helper.once("error", reject); });
    helper.unref();
    setImmediate(() => app.quit());
    return { quitting: true, transactionId: transaction.transactionId, revision: manifest.revision };
  } catch (error) {
    desktopInstallPending = false;
    throw error;
  }
}

async function installShellUpdate(installer) {
  if (desktopInstallPending) throw new Error("A desktop update is already pending");
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
    ? (async () => { if (await recoverInterruptedDesktopUpdate()) return null; return resolveRuntimeRoot(() => {}); })().then(() => null, (error) => error)
    : Promise.resolve(null);
  app.whenReady().then(async () => {
    const preparationError = await profilePreparation;
    if (processOwner.closing) return;
    if (preparationError instanceof Error) {
      mainWindow = createWindow();
      await loadStartup(mainWindow);
      mainWindow.webContents.send("startup:error", preparationError.message);
      return;
    }
    if (desktopRecoveryPending) return;
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
