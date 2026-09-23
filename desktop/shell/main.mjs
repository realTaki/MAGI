/** Electron desktop shell: native macOS controls and a local operator UI. */
import { spawn } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  renameSync,
  rmSync,
  statSync,
  watch,
} from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import dugite from "dugite";
import { app, BrowserWindow, Menu, clipboard, dialog, ipcMain, shell } from "electron";

const { resolveGitBinary, setupEnvironment } = dugite;
const SHELL_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SHELL_DIR, "..", "..");
const APP_DIST = path.join(SHELL_DIR, "..", "app", "dist", "index.html");
const UI_DEV_URL = process.env.MAGI_UI_URL ?? "http://127.0.0.1:5173";
const ASP_ORIGIN = new URL("http://127.0.0.1:42069");
const MAGI_REPOSITORY =
  process.env.MAGI_REPOSITORY_URL ?? "https://github.com/AgenticSocietyLab/MAGI.git";

let mainWindow = null;
// Child process for a locally started magi-asp. Not a delivery cache: ASP
// state stays in that process; desktop sqlite is Electron userData; MAGI has
// its own store.
let spawnedAsp = null;
let startingLocal = false;
let runtimeRootPromise = null;
let uiWatcher = null;
let uiReloadTimer = null;
let uiReloadPromptOpen = false;

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

async function cloneMagiSource(tools, report) {
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

  report("clone", "Cloning MAGI source…");
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

// The operator interface and the app's local logic both come from the
// checkout; the shell only loads them and forwards calls. Editing either one
// takes effect without reinstalling the app.
let localApi = null;
let localApiError = null;

function gitToolchain() {
  if (app.isPackaged) {
    return { binary: resolveGitBinary(), env: packagedTools().env };
  }
  return { binary: resolveGitBinary(), env: setupEnvironment({}).env };
}

async function loadLocalApp(checkout) {
  const entry = path.join(checkout, "desktop", "app", "main", "index.mjs");
  localApi = null;
  localApiError = null;
  if (!existsSync(entry)) {
    localApiError = new Error(`The app backend is missing from the checkout: ${entry}`);
    console.error(`[magi-app] ${localApiError.message}`);
    return;
  }
  try {
    // Cache-bust on mtime so an edited backend is picked up on the next launch.
    const backend = await import(`${pathToFileURL(entry).href}?v=${statSync(entry).mtimeMs}`);
    localApi = backend.createLocalApi({
      paths: { checkout, home: app.getPath("home"), userData: app.getPath("userData") },
      repository: MAGI_REPOSITORY,
      git: gitToolchain(),
      emit: (event, payload) => sendToWindow(mainWindow, "local:event", { event, payload }),
      openExternal: (url) => shell.openExternal(url),
      copy: (text) => clipboard.writeText(text),
    });
  } catch (error) {
    localApiError = error instanceof Error ? error : new Error(String(error));
    console.error(`[magi-app] could not load the local backend: ${localApiError.message}`);
  }
}

async function prepareCheckout(checkout, tools, report) {
  const asp = path.join(checkout, "magi-asp");
  const magi = path.join(checkout, "py-magi");
  const desktopApp = path.join(checkout, "desktop", "app");
  const requiredFiles = [
    path.join(asp, "pyproject.toml"),
    path.join(magi, "pyproject.toml"),
    path.join(desktopApp, "package-lock.json"),
  ];
  for (const required of requiredFiles) {
    if (!existsSync(required)) {
      throw new Error(`MAGI checkout is incomplete: ${required} is missing`);
    }
  }

  report("asp", "Preparing local magi-asp…");
  await command(tools.uv, ["sync", "--frozen", "--python", tools.python], {
    cwd: asp,
    env: tools.env,
    description: "Could not prepare magi-asp",
  });
  report("magi", "Preparing MAGI…");
  await command(tools.uv, ["sync", "--frozen", "--extra", "eva", "--python", tools.python], {
    cwd: magi,
    env: tools.env,
    description: "Could not prepare MAGI",
  });
  report("app-dependencies", "Installing app dependencies…");
  await command(tools.node, [tools.npm, "ci"], {
    cwd: desktopApp,
    env: tools.env,
    description: "Could not install the desktop app dependencies",
  });
  report("app-build", "Building the app…");
  await command(tools.node, [tools.npm, "run", "build"], {
    cwd: desktopApp,
    env: tools.env,
    description: "Could not build the desktop app",
  });
}

async function resolveRuntimeRoot(report) {
  if (!app.isPackaged) {
    const override = process.env.MAGI_DEV_CHECKOUT ?? "";
    if (override === "") {
      return REPO_ROOT;
    }
    // Dev shells have no bundled runtime, but dugite still provides Git, so a
    // scratch checkout keeps the local GitHub capability testable.
    const checkout = path.resolve(override);
    if (!existsSync(path.join(checkout, ".git"))) {
      throw new Error(`MAGI_DEV_CHECKOUT is not a Git checkout: ${checkout}`);
    }
    report("clone", `Using the checkout at ${checkout}`);
    return checkout;
  }
  if (runtimeRootPromise === null) {
    runtimeRootPromise = (async () => {
      const tools = packagedTools();
      const checkout = await cloneMagiSource(tools, report);
      await prepareCheckout(checkout, tools, report);
      return checkout;
    })().catch((error) => {
      runtimeRootPromise = null;
      throw error;
    });
  }
  return runtimeRootPromise;
}

function healthUrl() {
  return new URL("/health", ASP_ORIGIN).href;
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

function resolveAspPython(aspDir) {
  const unix = path.join(aspDir, ".venv", "bin", "python");
  const win = path.join(aspDir, ".venv", "Scripts", "python.exe");
  if (existsSync(unix)) {
    return unix;
  }
  if (existsSync(win)) {
    return win;
  }
  return process.platform === "win32" ? "python" : "python3";
}

function spawnLocalAsp(checkout) {
  // Desktop starts the local magi-asp. ASP spawns MAGI processes from this checkout.
  const aspDir = path.join(checkout, "magi-asp");
  const python = resolveAspPython(aspDir);
  const origin = ASP_ORIGIN;
  const env = app.isPackaged ? packagedTools().env : process.env;
  const child = spawn(python, ["main.py"], {
    cwd: aspDir,
    env: {
      ...env,
      PYTHONUNBUFFERED: "1",
      MAGI_SOURCE_DIR: checkout,
      MAGI_ASP_HOST: origin.hostname,
      MAGI_ASP_PORT: origin.port || "42069",
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

async function startLocalAsp(report) {
  report("checking", "Checking local magi-asp…");
  if (await isAspHealthy()) {
    return resolveRuntimeRoot(report);
  }
  const checkout = await resolveRuntimeRoot(report);
  const aspDir = path.join(checkout, "magi-asp");
  if (!existsSync(path.join(aspDir, "main.py"))) {
    throw new Error(`magi-asp was not found at ${aspDir}`);
  }
  report("starting", "Starting local magi-asp…");
  spawnedAsp = spawnLocalAsp(checkout);
  try {
    await new Promise((resolve, reject) => {
      spawnedAsp.once("error", reject);
      spawnedAsp.once("spawn", resolve);
    });
    report("starting", "Waiting for local magi-asp…");
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
  return checkout;
}

function reportStartup(win, step, message) {
  sendToWindow(win, "asp:startup-progress", { step, message });
}

async function launchLocalOperator(win) {
  if (startingLocal) {
    return;
  }
  startingLocal = true;
  try {
    const checkout = await startLocalAsp((step, message) => reportStartup(win, step, message));
    await loadLocalApp(checkout);
    if (!win.isDestroyed()) {
      await loadOperatorUi(win, checkout);
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

function stopWatchingOperatorUi() {
  uiWatcher?.close();
  uiWatcher = null;
  if (uiReloadTimer !== null) {
    clearTimeout(uiReloadTimer);
    uiReloadTimer = null;
  }
}

function watchOperatorUi(win, indexFile) {
  stopWatchingOperatorUi();
  uiWatcher = watch(path.dirname(indexFile), (_event, filename) => {
    if (filename !== null && String(filename) !== path.basename(indexFile)) {
      return;
    }
    if (uiReloadTimer !== null) {
      clearTimeout(uiReloadTimer);
    }
    uiReloadTimer = setTimeout(async () => {
      uiReloadTimer = null;
      if (uiReloadPromptOpen || win.isDestroyed() || !existsSync(indexFile)) {
        return;
      }
      uiReloadPromptOpen = true;
      try {
        const { response } = await dialog.showMessageBox(win, {
          type: "info",
          title: "MAGI interface updated",
          message: "A new local MAGI interface is ready.",
          detail: "Reload now to use the newly built interface?",
          buttons: ["Reload", "Later"],
          defaultId: 0,
          cancelId: 1,
        });
        if (response === 0 && !win.isDestroyed()) {
          await win.loadFile(indexFile);
        }
      } finally {
        uiReloadPromptOpen = false;
      }
    }, 500);
  });
  uiWatcher.once("error", stopWatchingOperatorUi);
}

async function loadStartup(win) {
  stopWatchingOperatorUi();
  await win.loadFile(path.join(SHELL_DIR, "ui", "index.html"));
}

async function loadOperatorUi(win, checkout) {
  const builtUi = app.isPackaged
    ? path.join(checkout, "desktop", "app", "dist", "index.html")
    : APP_DIST;
  if (existsSync(builtUi) && !process.env.MAGI_UI_URL) {
    await win.loadFile(builtUi);
    watchOperatorUi(win, builtUi);
  } else {
    stopWatchingOperatorUi();
    await win.loadURL(UI_DEV_URL);
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
      stopWatchingOperatorUi();
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
  const handler = localApi[method];
  if (typeof handler !== "function") {
    throw new Error(`Unknown app method: ${String(method)}`);
  }
  return await handler(payload);
});

ipcMain.handle("asp:retry", async () => {
  if (mainWindow !== null) {
    await loadStartup(mainWindow);
    void launchLocalOperator(mainWindow);
  }
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
  stopWatchingOperatorUi();
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
