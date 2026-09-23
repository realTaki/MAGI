/** Electron desktop shell: native macOS controls and a local operator UI. */
import { spawn } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  watch,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import dugite from "dugite";
import { app, BrowserWindow, Menu, clipboard, dialog, ipcMain, shell } from "electron";

const { resolveGitBinary, setupEnvironment } = dugite;
const SHELL_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SHELL_DIR, "..", "..");
const UI_DIST = path.join(SHELL_DIR, "..", "ui", "dist", "index.html");
const UI_DEV_URL = process.env.MAGI_UI_URL ?? "http://127.0.0.1:5173";
const ASP_ORIGIN = new URL("http://127.0.0.1:42069");
const MAGI_REPOSITORY =
  process.env.MAGI_REPOSITORY_URL ?? "https://github.com/AgenticSocietyLab/MAGI.git";
// Public client ID of the MAGI GitHub OAuth app (device flow enabled, "expire
// user access tokens" off). Device flow needs no client secret, so every build
// ships the same ID and operators authorize with their own account. Rebranded
// builds point MAGI_GITHUB_CLIENT_ID at their own app.
const MAGI_GITHUB_CLIENT_ID = "Ov23li74Up8NcM5yCb61";
const GITHUB_CLIENT_ID =
  process.env.MAGI_GITHUB_CLIENT_ID?.trim() || MAGI_GITHUB_CLIENT_ID;
const GITHUB_API = "https://api.github.com";
const GITHUB_WEB = "https://github.com";
const GITHUB_DEVICE_URL = `${GITHUB_WEB}/login/device`;
const GITHUB_TOKEN_SCOPE = "repo read:user";
const GITHUB_TIMEOUT_MS = 20_000;

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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

function magiHome() {
  return path.join(app.getPath("home"), ".magi");
}

function githubTokenFile() {
  return path.join(magiHome(), "github-token");
}

// The token doubles as a Git credential: the checkout's local credential helper
// reads this file, so pushes to the fork need no token inside .git/config.
function readGitHubToken() {
  try {
    const token = readFileSync(githubTokenFile(), "utf8").trim();
    return token === "" ? null : token;
  } catch {
    return null;
  }
}

function writeGitHubToken(token) {
  mkdirSync(magiHome(), { recursive: true });
  writeFileSync(githubTokenFile(), `${token}\n`, { mode: 0o600 });
  chmodSync(githubTokenFile(), 0o600);
}

function clearGitHubToken() {
  rmSync(githubTokenFile(), { force: true });
}

async function githubApi(endpoint, { token, method = "GET", body } = {}) {
  const response = await fetch(`${GITHUB_API}${endpoint}`, {
    method,
    headers: {
      Accept: "application/vnd.github+json",
      "User-Agent": "MAGI-desktop",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(GITHUB_TIMEOUT_MS),
  });
  const payload = await response.text();
  let data = null;
  if (payload !== "") {
    try {
      data = JSON.parse(payload);
    } catch {
      data = { message: payload.slice(0, 300) };
    }
  }
  if (!response.ok) {
    throw new Error(
      `GitHub ${method} ${endpoint} failed (${response.status}): ${data?.message ?? response.statusText}`,
    );
  }
  return data;
}

async function requestDeviceCode() {
  const response = await fetch(`${GITHUB_WEB}/login/device/code`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "User-Agent": "MAGI-desktop",
    },
    body: JSON.stringify({ client_id: GITHUB_CLIENT_ID, scope: GITHUB_TOKEN_SCOPE }),
    signal: AbortSignal.timeout(GITHUB_TIMEOUT_MS),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || typeof data?.device_code !== "string") {
    const detail = data?.error_description ?? data?.error ?? response.statusText;
    throw new Error(`GitHub did not return a sign-in code (${detail}).`);
  }
  return data;
}

async function pollDeviceToken({ device_code, interval, expires_in }) {
  const deadline = Date.now() + Math.max(60, Number(expires_in) || 900) * 1000;
  let waitMs = Math.max(1, Number(interval) || 5) * 1000;
  while (Date.now() < deadline) {
    await delay(waitMs);
    const response = await fetch(`${GITHUB_WEB}/login/oauth/access_token`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "User-Agent": "MAGI-desktop",
      },
      body: JSON.stringify({
        client_id: GITHUB_CLIENT_ID,
        device_code,
        grant_type: "urn:ietf:params:oauth:grant-type:device_code",
      }),
      signal: AbortSignal.timeout(GITHUB_TIMEOUT_MS),
    });
    const data = await response.json().catch(() => null);
    if (typeof data?.access_token === "string") {
      return data.access_token;
    }
    switch (data?.error) {
      case "authorization_pending":
        break;
      case "slow_down":
        waitMs += 5_000;
        break;
      case "expired_token":
        throw new Error("The GitHub sign-in code expired. Sign in again.");
      case "access_denied":
        throw new Error("GitHub sign-in was cancelled.");
      default:
        throw new Error(data?.error_description ?? data?.error ?? "GitHub sign-in failed.");
    }
  }
  throw new Error("Timed out waiting for GitHub sign-in.");
}

function parseGitHubSlug(url) {
  const match = /github\.com[/:]([^/]+)\/([^/]+?)(?:\.git)?\/?$/i.exec(url.trim());
  if (!match) {
    throw new Error(`MAGI repository URL is not a GitHub repository: ${url}`);
  }
  return { owner: match[1], name: match[2] };
}

async function waitForFork(token, login, name) {
  const deadline = Date.now() + 60_000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      return await githubApi(`/repos/${login}/${name}`, { token });
    } catch (error) {
      lastError = error;
      await delay(2_000);
    }
  }
  throw new Error(
    `GitHub is still creating ${login}/${name}: ${lastError?.message ?? "timed out"}`,
  );
}

// Forks upstream into the signed-in account unless that fork already exists.
async function ensureFork(token, login, upstream, report) {
  const owned = await githubApi(`/repos/${login}/${upstream.name}`, { token }).catch(() => null);
  if (owned !== null) {
    if (owned.fork !== true) {
      throw new Error(
        `${login}/${upstream.name} already exists and is not a fork of ${upstream.owner}/${upstream.name}. Rename or delete it, then retry.`,
      );
    }
    report({ step: "forked", fork: owned.full_name, created: false });
    return owned;
  }

  report({ step: "forking", fork: `${login}/${upstream.name}` });
  await githubApi(`/repos/${upstream.owner}/${upstream.name}/forks`, {
    token,
    method: "POST",
    body: { default_branch_only: false },
  }).catch(async (error) => {
    // A fork created between the lookup and this call makes the API answer 422.
    const raced = await githubApi(`/repos/${login}/${upstream.name}`, { token }).catch(() => null);
    if (raced === null) {
      throw error;
    }
    return raced;
  });

  const fork = await waitForFork(token, login, upstream.name);
  report({ step: "forked", fork: fork.full_name, created: true });
  return fork;
}

async function gitConfigValue(tools, checkout, key) {
  try {
    return (
      await command(tools.git, ["config", "--local", "--get", key], {
        cwd: checkout,
        env: tools.env,
        description: `Could not read the Git setting ${key}`,
      })
    ).trim();
  } catch {
    return "";
  }
}

async function remoteUrl(tools, checkout, name) {
  try {
    return (
      await command(tools.git, ["remote", "get-url", name], {
        cwd: checkout,
        env: tools.env,
        description: `Could not read the ${name} remote`,
      })
    ).trim();
  } catch {
    return "";
  }
}

// The helper reads the token file at run time, so rotating the token needs no
// Git change; the empty entry first drops any helper inherited from global
// configuration for this checkout. Only shell builtins are used, so the helper
// works no matter which PATH another Git process runs with.
function credentialHelperValue() {
  const file = githubTokenFile().split(path.sep).join("/");
  return `!f() { read -r MAGI_GITHUB_TOKEN < "${file}"; echo username=x-access-token; echo "password=$MAGI_GITHUB_TOKEN"; }; f`;
}

async function assignGitIdentity(tools, checkout, viewer) {
  if (typeof viewer?.login !== "string" || viewer.login === "") {
    return;
  }
  const name =
    typeof viewer.name === "string" && viewer.name.trim() !== "" ? viewer.name.trim() : viewer.login;
  const email = Number.isInteger(viewer.id)
    ? `${viewer.id}+${viewer.login}@users.noreply.github.com`
    : "";
  const options = {
    cwd: checkout,
    env: tools.env,
    description: "Could not set the Git commit identity",
  };
  if ((await gitConfigValue(tools, checkout, "user.name")) === "") {
    await command(tools.git, ["config", "--local", "user.name", name], options);
  }
  if (email !== "" && (await gitConfigValue(tools, checkout, "user.email")) === "") {
    await command(tools.git, ["config", "--local", "user.email", email], options);
  }
}

// Points the checkout at the operator's fork and keeps upstream available for
// merges: origin is the fork, upstream is the repository it was cloned from.
async function pointCheckoutAtFork(checkout, tools, fork, upstream, viewer, report) {
  const forkUrl = `https://github.com/${fork.full_name}.git`;
  const upstreamUrl = `https://github.com/${upstream.owner}/${upstream.name}.git`;
  const options = { cwd: checkout, env: tools.env };
  const originUrl = await remoteUrl(tools, checkout, "origin");
  const trackedUpstream = await remoteUrl(tools, checkout, "upstream");

  report({ step: "remote", remote: forkUrl });
  await command(
    tools.git,
    originUrl === ""
      ? ["remote", "add", "origin", forkUrl]
      : ["remote", "set-url", "origin", forkUrl],
    { ...options, description: "Could not point origin at your fork" },
  );
  await command(
    tools.git,
    trackedUpstream === ""
      ? ["remote", "add", "upstream", upstreamUrl]
      : ["remote", "set-url", "upstream", upstreamUrl],
    { ...options, description: "Could not configure the upstream remote" },
  );
  await command(tools.git, ["config", "--local", "--replace-all", "credential.helper", ""], {
    ...options,
    description: "Could not reset the local Git credential helper",
  });
  await command(tools.git, ["config", "--local", "--add", "credential.helper", credentialHelperValue()], {
    ...options,
    description: "Could not store the Git credential helper",
  });
  await assignGitIdentity(tools, checkout, viewer);
}

// The GitHub connection belongs to this machine, not to ASP or MAGI: it forks
// the repository into the operator's account and repoints this checkout at the
// fork. The operator UI drives it over IPC and the result is stored locally;
// nothing here is part of the startup sequence.
let localCheckout = null;
let githubTools = null;
let deviceFlowPending = false;

function githubMetadataFile() {
  return path.join(app.getPath("userData"), "github.json");
}

function readGitHubMetadata() {
  try {
    const parsed = JSON.parse(readFileSync(githubMetadataFile(), "utf8"));
    return parsed !== null && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeGitHubMetadata(metadata) {
  const file = githubMetadataFile();
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(metadata, null, 2)}\n`);
}

function githubEvent(payload) {
  sendToWindow(mainWindow, "github:event", payload);
}

function requireCheckout() {
  if (localCheckout === null || githubTools === null) {
    throw new Error("This build has no local checkout to connect.");
  }
  return localCheckout;
}

// A build may point MAGI_REPOSITORY_URL at a mirror that is not on GitHub; the
// operator UI hides the connection step in that case instead of failing.
function upstreamSlug() {
  try {
    return parseGitHubSlug(MAGI_REPOSITORY);
  } catch {
    return null;
  }
}

async function currentGitHubState() {
  const upstream = upstreamSlug();
  const metadata = readGitHubMetadata();
  const token = readGitHubToken();
  const state = {
    available: localCheckout !== null && upstream !== null,
    upstream: upstream === null ? "" : `${upstream.owner}/${upstream.name}`,
    login: typeof metadata.login === "string" ? metadata.login : "",
    fork: typeof metadata.fork === "string" ? metadata.fork : "",
    signedIn: token !== null,
    verified: false,
    connected: false,
  };
  if (token === null) {
    return state;
  }
  const viewer = await githubApi("/user", { token }).catch(() => null);
  if (viewer === null) {
    clearGitHubToken();
    state.signedIn = false;
    return state;
  }
  state.verified = true;
  state.login = viewer.login;
  state.connected = state.fork !== "";
  return state;
}

// Device flow: the operator approves a one-time code in the browser, and the
// token lands in ~/.magi/github-token where the checkout's credential helper
// picks it up.
async function signInWithGitHub() {
  if (deviceFlowPending) {
    throw new Error("A GitHub sign-in is already running.");
  }
  deviceFlowPending = true;
  try {
    const device = await requestDeviceCode();
    githubEvent({
      step: "waiting",
      userCode: String(device.user_code ?? ""),
      expiresInMinutes: Math.max(1, Math.round((Number(device.expires_in) || 900) / 60)),
    });
    clipboard.writeText(String(device.user_code ?? ""));
    await shell.openExternal(GITHUB_DEVICE_URL).catch(() => {});
    const viewer = await acceptGitHubToken(await pollDeviceToken(device));
    writeGitHubMetadata({ ...readGitHubMetadata(), login: viewer.login });
    githubEvent({ step: "signed-in", login: viewer.login });
    return { login: viewer.login };
  } finally {
    deviceFlowPending = false;
  }
}

async function acceptGitHubToken(token) {
  const viewer = await githubApi("/user", { token });
  if (typeof viewer?.login !== "string" || viewer.login === "") {
    throw new Error("GitHub did not return an account for this token.");
  }
  writeGitHubToken(token);
  return viewer;
}

// Fork when the account has none, then wire the checkout: origin is the fork
// and upstream stays the repository it was cloned from.
async function connectLocalCheckout() {
  const checkout = requireCheckout();
  const upstream = upstreamSlug();
  if (upstream === null) {
    throw new Error(`MAGI_REPOSITORY_URL is not a GitHub repository: ${MAGI_REPOSITORY}`);
  }
  const token = readGitHubToken();
  if (token === null) {
    throw new Error("Sign in with GitHub first.");
  }
  const viewer = await githubApi("/user", { token });
  const fork = await ensureFork(token, viewer.login, upstream, githubEvent);
  await pointCheckoutAtFork(checkout, githubTools, fork, upstream, viewer, githubEvent);
  writeGitHubMetadata({ login: viewer.login, fork: fork.full_name, connectedAt: Date.now() });
  githubEvent({ step: "connected", login: viewer.login, fork: fork.full_name });
  return await currentGitHubState();
}

async function prepareCheckout(checkout, tools, report) {
  const asp = path.join(checkout, "magi-asp");
  const magi = path.join(checkout, "py-magi");
  const ui = path.join(checkout, "desktop", "ui");
  const requiredFiles = [
    path.join(asp, "pyproject.toml"),
    path.join(magi, "pyproject.toml"),
    path.join(ui, "package-lock.json"),
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
  report("ui-dependencies", "Installing MAGI interface dependencies…");
  await command(tools.node, [tools.npm, "ci"], {
    cwd: ui,
    env: tools.env,
    description: "Could not install the MAGI UI dependencies",
  });
  report("ui-build", "Building MAGI interface…");
  await command(tools.node, [tools.npm, "run", "build"], {
    cwd: ui,
    env: tools.env,
    description: "Could not build the MAGI UI",
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
    localCheckout = checkout;
    githubTools = { git: resolveGitBinary(), env: setupEnvironment({}).env };
    return checkout;
  }
  if (runtimeRootPromise === null) {
    runtimeRootPromise = (async () => {
      const tools = packagedTools();
      const checkout = await cloneMagiSource(tools, report);
      localCheckout = checkout;
      githubTools = tools;
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
    ? path.join(checkout, "desktop", "ui", "dist", "index.html")
    : UI_DIST;
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

// Local capabilities the operator UI drives. They stay out of the startup
// sequence: the app runs, then the operator connects GitHub when they want to.
ipcMain.handle("github:state", async () => await currentGitHubState());

ipcMain.handle("github:sign-in", async () => await signInWithGitHub());

ipcMain.handle("github:connect", async () => await connectLocalCheckout());

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
