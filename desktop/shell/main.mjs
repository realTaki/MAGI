/**
 * Electron shell. It prepares the environment — bundled tools, the checkout,
 * ASP — and opens a window. The app in desktop/app is the interface and the
 * local backend; add a capability there, not in this file.
 */
import { spawn } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  renameSync,
  rmSync,
  statSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import dugite from "dugite";
import { app, BrowserWindow, Menu, clipboard, ipcMain, shell } from "electron";

const { resolveGitBinary, setupEnvironment } = dugite;
const SHELL_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SHELL_DIR, "..", "..");
const MAGI_REPOSITORY =
  process.env.MAGI_REPOSITORY_URL ?? "https://github.com/AgenticSocietyLab/MAGI.git";

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
  const runtime = path.join(process.resourcesPath, "runtime");
  const node = path.join(runtime, "bin", process.platform === "win32" ? "node.exe" : "node");
  const npm = path.join(runtime, "npm", "node_modules", "npm", "bin", "npm-cli.js");
  const uv = path.join(
    runtime,
    "bin",
    process.platform === "win32" ? "uv.exe" : "uv",
  );
  const pythonCandidates =
    process.platform === "win32"
      ? [path.join(runtime, "python", "python.exe")]
      : [
          path.join(runtime, "python", "bin", "python3.12"),
          path.join(runtime, "python", "bin", "python3"),
          path.join(runtime, "python", "bin", "python"),
        ];
  const python = pythonCandidates.find(existsSync);
  if (!python || !existsSync(node) || !existsSync(npm) || !existsSync(uv)) {
    throw new Error("MAGI.app is missing its bundled Python or Node.js runtime");
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
    UV_NO_MANAGED_PYTHON: "1",
  };
  return { env, git, node, npm, python, uv };
}

async function cloneMagiSource(tools, progress) {
  const destination = path.join(app.getPath("home"), ".magi", "MAGI");
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
// Where the bundled runtime is, when this build has one. The app starts every
// child process from this: uv for the Python environments, npm for the build.
let localTools = null;
// Only a checkout this app owns (packaged, or an explicit scratch checkout) may
// be rewired; a developer's own working tree must stay untouched.
let localCheckoutManaged = false;

// Unpackaged runs borrow the developer's tools instead.
function devTools() {
  return {
    node: "node",
    npm: "npm",
    python: process.platform === "win32" ? "python" : "python3",
    uv: "uv",
    git: resolveGitBinary(),
    env: { ...setupEnvironment({}).env },
  };
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
      },
      repository: MAGI_REPOSITORY,
      managed: localCheckoutManaged,
      tools: localTools ?? devTools(),
      emit: (event, payload) => sendToWindow(mainWindow, "local:event", { event, payload }),
      openExternal: (url) => shell.openExternal(url),
      copy: (text) => clipboard.writeText(text),
    });
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
    // Dev shells have no bundled runtime, but dugite still provides Git, so a
    // scratch checkout keeps the local capabilities testable.
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
    // A retry replaces the backend, so let the previous one stop what it started.
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

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 900,
    minHeight: 600,
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
  // The app owns the processes it started (local ASP, for one).
  localApi?.dispose?.();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
