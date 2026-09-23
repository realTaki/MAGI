/**
 * The desktop app's local backend.
 *
 * The Electron shell loads this module from the checkout, so everything in
 * here evolves with the source tree instead of with the installed package: the
 * shell only knows how to load it and how to forward calls to it.
 *
 * The shell hands over a context (paths, Git, and small native helpers) and
 * gets back a table of methods that the operator interface reaches through the
 * ``local:invoke`` bridge. Nothing here talks to ASP or MAGI.
 *
 * The shell calls the methods it knows about and nothing else:
 *   prepare(progress)        — get this checkout ready and report the interface
 *                              entry point (a file path or a URL).
 *   start(progress)          — bring up local ASP, the service this app talks to.
 *   dispose()                — stop what this instance started.
 *   github.state             — account, fork and whether the checkout is wired.
 *   github.signIn            — OAuth device flow; stores the token for Git to use.
 *   github.connect           — fork when the account has none, then point the
 *                              checkout at it.
 *   provider.settings/save   — keep the key in app data and sync through ASP.
 *
 * ``progress`` is ``(message, percent)`` with an absolute 0..1 percentage.
 */

import { spawn } from "node:child_process";
import { chmodSync, copyFileSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { openChatStore } from "./chat-store.mjs";

const ASP_ORIGIN = new URL("http://127.0.0.1:42069");
// A brand-new society should not be an empty room. ASP names MAGI eva-000,
// eva-001, … as they are created; these are the nicknames of the first three.
const DEFAULT_MAGIS = ["MELCHIOR", "BALTHASAR", "CASPER"];
const MAGI_ONLINE_TIMEOUT_MS = 30_000;
const GITHUB_API = "https://api.github.com";
const GITHUB_WEB = "https://github.com";
const GITHUB_DEVICE_URL = `${GITHUB_WEB}/login/device`;
const GITHUB_TOKEN_SCOPE = "repo read:user";
// Public client ID of the MAGI GitHub OAuth app (device flow enabled, "expire
// user access tokens" off). Device flow needs no client secret, so every build
// ships the same ID and operators authorize with their own account. Rebranded
// builds point MAGI_GITHUB_CLIENT_ID at their own app.
const MAGI_GITHUB_CLIENT_ID = "Ov23li74Up8NcM5yCb61";
const GITHUB_CLIENT_ID =
  (process.env.MAGI_GITHUB_CLIENT_ID ?? "").trim() || MAGI_GITHUB_CLIENT_ID;
const GITHUB_TIMEOUT_MS = 20_000;
const FORK_TIMEOUT_MS = 60_000;
// How often the checkout's commit is checked for the interface to rebuild.
const COMMIT_POLL_MS = 5_000;
const AVATAR_TIMEOUT_MS = 8_000;
const PROVIDER_POLL_MS = 5_000;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function command(binary, args, { cwd, env, description }) {
  return new Promise((resolve, reject) => {
    const child = spawn(binary, args, {
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

export function createLocalApi(context) {
  const { paths, repository, tools, emit, openExternal, copy, managed = false } = context;
  const git = { binary: tools.git, env: tools.env };
  const options = { cwd: paths.checkout, env: tools.env };
  let asp = null;
  let deviceFlowPending = false;
  let commitWatcher = null;
  let chatStorePromise = null;
  let providerTimer = null;
  let providerRelayReady = false;
  const providerDelivered = new Set();

  // A developer's own working tree is not this app's to rewire; only a checkout
  // the shell created (or one it was pointed at explicitly) counts.
  function requireManaged() {
    if (!managed) {
      throw new Error(
        "This checkout is not managed by the app. Use the packaged app, or point MAGI_DEV_CHECKOUT at a scratch checkout.",
      );
    }
  }

  const appData = path.join(paths.home, ".magi", "app");
  mkdirSync(appData, { recursive: true });
  function chatStore() {
    chatStorePromise ??= openChatStore(path.join(appData, "chat.sqlite"));
    return chatStorePromise;
  }
  // The app owns these files. Copy older locations once, without changing
  // Electron's Chromium profile or overwriting state already in appData.
  function migrateAppFile(name, legacy = []) {
    const destination = path.join(appData, name);
    if (!existsSync(destination)) {
      for (const source of [path.join(paths.userData, name), ...legacy]) {
        if (source !== destination && existsSync(source)) {
          copyFileSync(source, destination);
          break;
        }
      }
    }
    return destination;
  }
  const tokenPath = migrateAppFile("github-token", [path.join(paths.home, ".magi", "github-token")]);
  const metadataPath = migrateAppFile("github.json");
  const avatarPath = migrateAppFile("github-avatar");
  const providerPath = path.join(appData, "provider.json");
  if (existsSync(tokenPath)) chmodSync(tokenPath, 0o600);
  const tokenFile = () => tokenPath;
  const metadataFile = () => metadataPath;
  const avatarFile = () => avatarPath;
  // Metadata written before the picture was cached gets one download attempt per
  // account and run, so a blocked network cannot slow every state read.
  const avatarAttempted = new Set();

  // The token doubles as a Git credential: the checkout's local credential
  // helper reads this file, so pushes to the fork need no token in .git/config.
  function readToken() {
    try {
      const token = readFileSync(tokenFile(), "utf8").trim();
      return token === "" ? null : token;
    } catch {
      return null;
    }
  }

  function writeToken(token) {
    mkdirSync(path.dirname(tokenFile()), { recursive: true });
    writeFileSync(tokenFile(), `${token}\n`, { mode: 0o600 });
    chmodSync(tokenFile(), 0o600);
  }

  function clearToken() {
    rmSync(tokenFile(), { force: true });
  }

  function readMetadata() {
    try {
      const parsed = JSON.parse(readFileSync(metadataFile(), "utf8"));
      return parsed !== null && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function writeMetadata(metadata) {
    mkdirSync(path.dirname(metadataFile()), { recursive: true });
    writeFileSync(metadataFile(), `${JSON.stringify(metadata, null, 2)}\n`);
  }

  function normalizeProvider(settings) {
    return Object.fromEntries(
      ["provider", "model", "api_key"].map((field) => [
        field,
        typeof settings?.[field] === "string" ? settings[field].trim() || null : null,
      ]),
    );
  }

  function readProvider() {
    if (!existsSync(providerPath)) return null;
    return normalizeProvider(JSON.parse(readFileSync(providerPath, "utf8")));
  }

  function writeProvider(settings) {
    const temporary = `${providerPath}.${process.pid}.tmp`;
    writeFileSync(temporary, `${JSON.stringify(settings)}\n`, { mode: 0o600 });
    try {
      renameSync(temporary, providerPath);
      chmodSync(providerPath, 0o600);
    } finally {
      rmSync(temporary, { force: true });
    }
  }

  // The account's name and picture are this machine's state, so they live next
  // to the token. Caching the image keeps the avatar visible without the
  // interface reaching GitHub itself.
  async function cacheAvatar(url, fallbackType) {
    if (typeof url !== "string" || url === "") {
      return fallbackType;
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(AVATAR_TIMEOUT_MS) });
      if (!response.ok) {
        return fallbackType;
      }
      const bytes = Buffer.from(await response.arrayBuffer());
      mkdirSync(path.dirname(avatarFile()), { recursive: true });
      writeFileSync(avatarFile(), bytes);
      const type = response.headers.get("content-type") ?? "";
      return type.startsWith("image/") ? type : "image/png";
    } catch {
      return fallbackType;
    }
  }

  /** Records who is signed in, downloading the avatar when it is not cached yet. */
  async function rememberViewer(viewer, extra = {}) {
    const metadata = readMetadata();
    const avatarType = await cacheAvatar(
      viewer?.avatar_url,
      typeof metadata.avatarType === "string" ? metadata.avatarType : "image/png",
    );
    const written = {
      ...metadata,
      ...extra,
      login: viewer.login,
      name:
        typeof viewer.name === "string" && viewer.name.trim() !== ""
          ? viewer.name.trim()
          : viewer.login,
      avatarType,
    };
    writeMetadata(written);
    return written;
  }

  function avatarDataUrl(metadata) {
    try {
      const type = typeof metadata.avatarType === "string" ? metadata.avatarType : "image/png";
      return `data:${type};base64,${readFileSync(avatarFile()).toString("base64")}`;
    } catch {
      return "";
    }
  }

  // A build may point the clone source at a mirror that is not on GitHub; the
  // interface hides the connection step in that case instead of failing.
  function upstreamSlug() {
    try {
      return parseGitHubSlug(repository);
    } catch {
      return null;
    }
  }

  async function gitConfigValue(key) {
    try {
      return (await command(git.binary, ["config", "--local", "--get", key], {
        ...options,
        description: `Could not read the Git setting ${key}`,
      })).trim();
    } catch {
      return "";
    }
  }

  async function remoteUrl(name) {
    try {
      return (await command(git.binary, ["remote", "get-url", name], {
        ...options,
        description: `Could not read the ${name} remote`,
      })).trim();
    } catch {
      return "";
    }
  }

  // The helper reads the token file at run time, so rotating the token needs no
  // Git change; the empty entry first drops any helper inherited from global
  // configuration for this checkout. Only shell builtins are used, so the
  // helper works no matter which PATH another Git process runs with.
  function credentialHelperValue() {
    const file = tokenFile().split(path.sep).join("/");
    return `!f() { read -r MAGI_GITHUB_TOKEN < "${file}"; echo username=x-access-token; echo "password=$MAGI_GITHUB_TOKEN"; }; f`;
  }

  async function assignGitIdentity(viewer) {
    if (typeof viewer?.login !== "string" || viewer.login === "") {
      return;
    }
    const name =
      typeof viewer.name === "string" && viewer.name.trim() !== "" ? viewer.name.trim() : viewer.login;
    const email = Number.isInteger(viewer.id)
      ? `${viewer.id}+${viewer.login}@users.noreply.github.com`
      : "";
    if ((await gitConfigValue("user.name")) === "") {
      await command(git.binary, ["config", "--local", "user.name", name], {
        ...options,
        description: "Could not set the Git commit identity",
      });
    }
    if (email !== "" && (await gitConfigValue("user.email")) === "") {
      await command(git.binary, ["config", "--local", "user.email", email], {
        ...options,
        description: "Could not set the Git commit identity",
      });
    }
  }

  async function waitForFork(token, login, name) {
    const deadline = Date.now() + FORK_TIMEOUT_MS;
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

  // Forks upstream into the signed-in account, unless a repository with that
  // name is already there — an existing one is used as it is, fork or not.
  async function ensureFork(token, login, upstream) {
    const owned = await githubApi(`/repos/${login}/${upstream.name}`, { token }).catch(() => null);
    if (owned !== null) {
      emit("github.forked", { fork: owned.full_name, created: false });
      return owned;
    }

    emit("github.forking", { fork: `${login}/${upstream.name}` });
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
    emit("github.forked", { fork: fork.full_name, created: true });
    return fork;
  }

  // Points the checkout at the operator's fork and keeps upstream available for
  // merges: origin is the fork, upstream is the repository it was cloned from.
  async function pointCheckoutAtFork(fork, upstream, viewer) {
    const forkUrl = `https://github.com/${fork.full_name}.git`;
    const upstreamUrl = `https://github.com/${upstream.owner}/${upstream.name}.git`;

    emit("github.remote", { remote: forkUrl });
    const originUrl = await remoteUrl("origin");
    await command(
      git.binary,
      originUrl === ""
        ? ["remote", "add", "origin", forkUrl]
        : ["remote", "set-url", "origin", forkUrl],
      { ...options, description: "Could not point origin at your fork" },
    );
    const trackedUpstream = await remoteUrl("upstream");
    await command(
      git.binary,
      trackedUpstream === ""
        ? ["remote", "add", "upstream", upstreamUrl]
        : ["remote", "set-url", "upstream", upstreamUrl],
      { ...options, description: "Could not configure the upstream remote" },
    );
    await command(git.binary, ["config", "--local", "--replace-all", "credential.helper", ""], {
      ...options,
      description: "Could not reset the local Git credential helper",
    });
    await command(git.binary, ["config", "--local", "--add", "credential.helper", credentialHelperValue()], {
      ...options,
      description: "Could not store the Git credential helper",
    });
    await assignGitIdentity(viewer);
  }

  async function currentState() {
    const upstream = upstreamSlug();
    const metadata = readMetadata();
    const token = readToken();
    const state = {
      available: managed && upstream !== null,
      upstream: upstream === null ? "" : `${upstream.owner}/${upstream.name}`,
      login: typeof metadata.login === "string" ? metadata.login : "",
      name: typeof metadata.name === "string" ? metadata.name : "",
      avatar: "",
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
      clearToken();
      state.signedIn = false;
      return state;
    }
    let current = metadata;
    if (metadata.login !== viewer.login || !existsSync(avatarFile())) {
      if (!avatarAttempted.has(viewer.login)) {
        avatarAttempted.add(viewer.login);
        current = await rememberViewer(viewer);
      }
    }
    state.verified = true;
    state.login = viewer.login;
    state.name = typeof current.name === "string" ? current.name : viewer.login;
    state.avatar = avatarDataUrl(current);
    state.connected = state.fork !== "";
    return state;
  }

  async function signIn() {
    requireManaged();
    if (upstreamSlug() === null) {
      throw new Error(`MAGI_REPOSITORY_URL is not a GitHub repository: ${repository}`);
    }
    if (deviceFlowPending) {
      throw new Error("A GitHub sign-in is already running.");
    }
    deviceFlowPending = true;
    try {
      const device = await requestDeviceCode();
      emit("github.waiting", {
        userCode: String(device.user_code ?? ""),
        expiresInMinutes: Math.max(1, Math.round((Number(device.expires_in) || 900) / 60)),
      });
      copy(String(device.user_code ?? ""));
      await openExternal(GITHUB_DEVICE_URL).catch(() => {});
      const token = await pollDeviceToken(device);
      const viewer = await githubApi("/user", { token });
      if (typeof viewer?.login !== "string" || viewer.login === "") {
        throw new Error("GitHub did not return an account for this token.");
      }
      writeToken(token);
      await rememberViewer(viewer);
      emit("github.signed-in", { login: viewer.login });
      return { login: viewer.login };
    } finally {
      deviceFlowPending = false;
    }
  }

  async function connect() {
    requireManaged();
    const upstream = upstreamSlug();
    if (upstream === null) {
      throw new Error(`MAGI_REPOSITORY_URL is not a GitHub repository: ${repository}`);
    }
    const token = readToken();
    if (token === null) {
      throw new Error("Sign in with GitHub first.");
    }
    const viewer = await githubApi("/user", { token });
    const fork = await ensureFork(token, viewer.login, upstream);
    await pointCheckoutAtFork(fork, upstream, viewer);
    await rememberViewer(viewer, { fork: fork.full_name, connectedAt: Date.now() });
    emit("github.connected", { login: viewer.login, fork: fork.full_name });
    return await currentState();
  }

  // ---------------------------------------------------------------------
  // Preparing this checkout and running its local service
  // ---------------------------------------------------------------------

  function healthUrl() {
    return new URL("/health", ASP_ORIGIN).href;
  }

  async function isAspHealthy() {
    try {
      const response = await fetch(healthUrl(), { signal: AbortSignal.timeout(400) });
      return response.ok;
    } catch {
      return false;
    }
  }

  async function waitForAsp(timeoutMs = 20_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await isAspHealthy()) {
        return;
      }
      await delay(200);
    }
    throw new Error(`magi-asp is unavailable at ${healthUrl()}`);
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
    return tools.python;
  }

  function spawnAsp() {
    const aspDir = path.join(paths.checkout, "magi-asp");
    const child = spawn(resolveAspPython(aspDir), ["main.py"], {
      cwd: aspDir,
      env: {
        ...tools.env,
        PYTHONUNBUFFERED: "1",
        MAGI_SOURCE_DIR: paths.checkout,
        MAGI_ASP_HOST: ASP_ORIGIN.hostname,
        MAGI_ASP_PORT: ASP_ORIGIN.port || "42069",
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

  /** Interface entry point: the dev server when one is configured, else the build. */
  function uiEntry() {
    const devUrl = (process.env.MAGI_APP_URL ?? "").trim();
    if (devUrl !== "") {
      return devUrl;
    }
    const built = path.join(paths.checkout, "desktop", "app", "dist", "index.html");
    return existsSync(built) ? built : "http://127.0.0.1:5173";
  }

  /** The checkout's current commit, empty when Git cannot answer. */
  async function readCommit() {
    try {
      return (
        await command(git.binary, ["rev-parse", "HEAD"], {
          ...options,
          description: "Could not read the checkout commit",
        })
      ).trim();
    } catch {
      return "";
    }
  }

  /** Rebuild the interface from the checkout — what a new commit makes visible. */
  async function rebuildInterface() {
    const appDir = path.join(paths.checkout, "desktop", "app");
    const build = (description) =>
      command(tools.node, [tools.npm, "run", "build"], {
        cwd: appDir,
        env: tools.env,
        description,
      });
    try {
      await build("Could not rebuild the interface");
    } catch (error) {
      // A pulled commit can bring a new lockfile: install once, then try again.
      try {
        await command(tools.node, [tools.npm, "ci"], {
          cwd: appDir,
          env: tools.env,
          description: "Could not install the app dependencies",
        });
        await build("Could not rebuild the interface");
      } catch {
        throw error;
      }
    }
  }

  /**
   * The commit is the unit this app reacts to: a pull (or a local commit) in the
   * checkout rebuilds the interface, then tells the interface to offer a reload.
   * Files are ignored on purpose. A page reload leaves ASP and MAGI running.
   */
  function watchCheckout() {
    if (commitWatcher !== null || !managed || (process.env.MAGI_APP_URL ?? "").trim() !== "") {
      return;
    }
    const watcher = { commit: "", building: false, stopped: false, timer: null };
    commitWatcher = watcher;
    const poll = async () => {
      const commit = await readCommit();
      if (watcher.stopped) {
        return;
      }
      if (commit !== "" && commit !== watcher.commit) {
        const changed = watcher.commit !== "";
        watcher.commit = commit;
        if (changed && !watcher.building) {
          watcher.building = true;
          try {
            await rebuildInterface();
            if (!watcher.stopped) {
              emit("app.interface-updated", { commit });
            }
          } catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            console.error(`[magi-app] could not rebuild the interface: ${detail}`);
          } finally {
            watcher.building = false;
          }
        }
      }
      if (!watcher.stopped) {
        watcher.timer = setTimeout(() => void poll(), COMMIT_POLL_MS);
      }
    };
    void poll();
  }

  function stopWatchingCheckout() {
    if (commitWatcher === null) {
      return;
    }
    commitWatcher.stopped = true;
    clearTimeout(commitWatcher.timer);
    commitWatcher = null;
  }

  /**
   * Everything this checkout needs before it can run. Only a managed checkout
   * is prepared: a developer's own tree is already theirs to prepare.
   */
  async function prepare(progress) {
    const aspDir = path.join(paths.checkout, "magi-asp");
    const magiDir = path.join(paths.checkout, "py-magi");
    const appDir = path.join(paths.checkout, "desktop", "app");
    const required = [
      path.join(aspDir, "pyproject.toml"),
      path.join(magiDir, "pyproject.toml"),
      path.join(appDir, "package-lock.json"),
    ];
    for (const file of required) {
      if (!existsSync(file)) {
        throw new Error(`MAGI checkout is incomplete: ${file} is missing`);
      }
    }

    if (managed) {
      progress?.("Preparing local magi-asp…", 0.2);
      await command(tools.uv, ["sync", "--frozen", "--python", tools.python], {
        cwd: aspDir,
        env: tools.env,
        description: "Could not prepare magi-asp",
      });
      progress?.("Preparing MAGI…", 0.4);
      await command(
        tools.uv,
        ["sync", "--frozen", "--extra", "eva", "--python", tools.python],
        { cwd: magiDir, env: tools.env, description: "Could not prepare MAGI" },
      );
      progress?.("Installing app dependencies…", 0.6);
      await command(tools.node, [tools.npm, "ci"], {
        cwd: appDir,
        env: tools.env,
        description: "Could not install the desktop app dependencies",
      });
      progress?.("Building the app…", 0.8);
      await command(tools.node, [tools.npm, "run", "build"], {
        cwd: appDir,
        env: tools.env,
        description: "Could not build the desktop app",
      });
    }

    progress?.("Prepared.", 0.85);
    watchCheckout();
    return { ui: uiEntry() };
  }

  async function aspJson(endpoint, { token, method = "GET", body, timeoutMs = 20_000 } = {}) {
    const response = await fetch(new URL(endpoint, ASP_ORIGIN), {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    const text = await response.text();
    let data = null;
    if (text !== "") {
      try {
        data = JSON.parse(text);
      } catch {
        data = { detail: text.slice(0, 300) };
      }
    }
    if (!response.ok) {
      const error = new Error(
        `ASP ${method} ${endpoint} failed (${response.status}): ${data?.detail ?? response.statusText}`,
      );
      error.status = response.status;
      throw error;
    }
    return data;
  }

  async function migrateLegacyProvider(token) {
    const legacy = await aspJson("/settings/provider/legacy", { token });
    if (legacy === null) return;
    if (!existsSync(providerPath)) {
      writeProvider(normalizeProvider(legacy));
    } else {
      readProvider(); // Do not remove the old copy if the app's file is unreadable.
    }
    await aspJson("/settings/provider/legacy", { token, method: "DELETE" });
  }

  async function ensureProviderRelay(token) {
    if (providerRelayReady) return;
    await migrateLegacyProvider(token);
    providerRelayReady = true;
  }

  async function syncProvider(settings, handles, token) {
    return await aspJson("/settings/provider", {
      token,
      method: "PUT",
      body: { ...settings, ...(handles === null ? {} : { handles }) },
      timeoutMs: 30_000,
    });
  }

  async function saveProvider(input) {
    const settings = normalizeProvider(input);
    writeProvider(settings);
    providerDelivered.clear();
    try {
      const { token } = await aspJson("/operator");
      await ensureProviderRelay(token);
      const result = await syncProvider(settings, null, token);
      for (const handle of result.synced ?? []) providerDelivered.add(handle);
      return result;
    } catch (error) {
      throw new Error(`Saved in the app, but MAGI sync failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function watchProviderRecipients(token) {
    if (providerTimer !== null) return;
    const poll = async () => {
      try {
        const settings = readProvider();
        if (settings !== null) {
          const bots = (await aspJson("/bots", { token }))?.bots ?? [];
          const online = new Set(bots.filter((bot) => bot.online).map((bot) => bot.handle));
          for (const handle of providerDelivered) {
            if (!online.has(handle)) providerDelivered.delete(handle);
          }
          const pending = [...online].filter((handle) => !providerDelivered.has(handle));
          if (pending.length > 0) {
            const result = await syncProvider(settings, pending, token);
            for (const handle of result.synced ?? []) providerDelivered.add(handle);
          }
        }
      } catch (error) {
        console.error(`[magi-app] provider sync: ${error instanceof Error ? error.message : String(error)}`);
      }
      if (providerTimer !== null) {
        providerTimer = setTimeout(() => void poll(), PROVIDER_POLL_MS);
      }
    };
    providerTimer = setTimeout(() => void poll(), 0);
  }

  /** A freshly spawned MAGI answers on its socket only after it has booted. */
  async function nameWhenOnline(handle, nickname, token) {
    const deadline = Date.now() + MAGI_ONLINE_TIMEOUT_MS;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        await aspJson(`/bots/${encodeURIComponent(handle)}/nickname`, {
          token,
          method: "PATCH",
          body: { nickname },
        });
        return true;
      } catch (error) {
        // 503/504 mean "not connected yet"; anything else is a real answer.
        if (error?.status !== 503 && error?.status !== 504) {
          throw error;
        }
        lastError = error;
        await delay(1_000);
      }
    }
    throw new Error(`Could not name ${handle} ${nickname}: ${lastError?.message ?? "timed out"}`);
  }

  /**
   * Seeds the three MAGI the operator meets first, but only while the society is
   * still empty. Best effort on purpose: an unnamed MAGI is a cosmetic problem,
   * failing startup over it would not be.
   */
  async function ensureDefaultMagis(progress) {
    const { token } = await aspJson("/operator");
    const listed = (await aspJson("/bots", { token }))?.bots ?? [];
    if (listed.length > 0) {
      return;
    }
    progress?.("Creating default MAGIs…", 0.98);
    const created = [];
    for (const nickname of DEFAULT_MAGIS) {
      const conversation = await aspJson("/conversations", {
        token,
        method: "POST",
        body: { kind: "bot" },
      });
      const handle = conversation?.agents?.[0];
      if (typeof handle === "string" && handle !== "") {
        created.push({ handle, nickname });
      }
    }
    await Promise.all(
      created.map(({ handle, nickname }) =>
        nameWhenOnline(handle, nickname, token).catch((error) => {
          console.error(
            `[magi-asp] ${error instanceof Error ? error.message : String(error)}`,
          );
        }),
      ),
    );
  }

  async function activateProviderSync() {
    const { token } = await aspJson("/operator");
    try {
      await ensureProviderRelay(token);
      watchProviderRecipients(token);
    } catch (error) {
      console.error(`[magi-app] provider migration: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  /** Brings local ASP up unless something already answers on its port. */
  async function start(progress) {
    progress?.("Checking local magi-asp…", 0.9);
    if (await isAspHealthy()) {
      await ensureDefaultMagis(progress);
      await activateProviderSync();
      return { origin: ASP_ORIGIN.href };
    }
    const aspDir = path.join(paths.checkout, "magi-asp");
    if (!existsSync(path.join(aspDir, "main.py"))) {
      throw new Error(`magi-asp was not found at ${aspDir}`);
    }
    progress?.("Starting local magi-asp…", 0.94);
    asp = spawnAsp();
    try {
      await new Promise((resolve, reject) => {
        asp.once("error", reject);
        asp.once("spawn", resolve);
      });
      progress?.("Waiting for local magi-asp…", 0.97);
      await Promise.race([
        waitForAsp(),
        new Promise((_, reject) => {
          asp.once("exit", (code, signal) => {
            reject(new Error(`magi-asp exited (${code ?? signal ?? "unknown"})`));
          });
        }),
      ]);
    } catch (error) {
      dispose();
      throw error;
    }
    await ensureDefaultMagis(progress);
    await activateProviderSync();
    return { origin: ASP_ORIGIN.href };
  }

  function dispose() {
    stopWatchingCheckout();
    if (chatStorePromise !== null) {
      void chatStorePromise.then((store) => store.close());
      chatStorePromise = null;
    }
    if (providerTimer !== null) clearTimeout(providerTimer);
    providerTimer = null;
    if (asp && !asp.killed) {
      asp.kill("SIGTERM");
    }
    asp = null;
  }

  return {
    prepare,
    start,
    dispose,
    "github.state": currentState,
    "github.signIn": signIn,
    "github.connect": connect,
    "provider.settings": () => readProvider() ?? normalizeProvider(null),
    "provider.save": saveProvider,
    "chat.listConversations": async () => (await chatStore()).listConversations(),
    "chat.saveConversations": async (rows) => (await chatStore()).saveConversations(rows),
    "chat.listEvents": async (id) => (await chatStore()).listEvents(id),
    "chat.saveEvents": async ({ id, events }) => (await chatStore()).saveEvents(id, events),
    "chat.lastSequence": async (id) => (await chatStore()).lastSequence(id),
    "chat.pendingAcks": async (id) => (await chatStore()).pendingAcks(id),
    "chat.markAcknowledged": async ({ id, sequence }) =>
      (await chatStore()).markAcknowledged(id, sequence),
  };
}
