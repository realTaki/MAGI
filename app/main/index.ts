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
 *   dispose()                — release reloadable backend resources only.
 *   shutdown()               — stop ASP when the desktop itself exits.
 *   github.state             — account, fork and whether the checkout is wired.
 *   github.signIn            — OAuth device flow; stores the token for Git to use.
 *   github.connect           — fork when the account has none, then point the
 *                              checkout at it.
 *   provider.settings/save   — keep the key in app data and sync through ASP.
 *   source.status            — branch, commit, and how this checkout sits
 *                              against the AgenticSociety remote.
 *
 * ``progress`` is ``(message, percent)`` with an absolute 0..1 percentage.
 */

import { spawn } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { openChatStore } from "./chat-store.mjs";
import { agentBranch, createMagiRuntime } from "./magi-runtime.mjs";
import { downloadShellInstaller, installerPath, shellRelease } from "./shell-update.mjs";

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
  const { paths, repository, tools, emit, openExternal, copy, shellUpdate, managed = false } = context;
  // Shell loads this backend from the App worktree, while the source checkout
  // remains the Git/worktree authority for the local runtime.
  const appCheckout = paths.appCheckout ?? paths.checkout;
  let aspCheckout = paths.checkout;
  const spawnProcess = context.spawn ?? spawn;
  const git = { binary: tools.git, env: tools.env };
  const options = { cwd: paths.checkout, env: tools.env };
  let asp = null;
  let deviceFlowPending = false;
  let runtimeBusy = false;
  let chatStorePromise = null;
  let providerTimer = null;
  let providerRelayReady = false;
  const providerAttempted = new Set();
  // ASP keeps the roll of agents; this backend owns their source trees and
  // processes (see magi-runtime.mjs). A developer's own checkout is never
  // rewired, so MAGI then share it exactly as before.
  const magiRuntime = createMagiRuntime({
    checkout: paths.checkout,
    home: paths.home,
    base: ASP_ORIGIN.href,
    tools,
    run: command,
    spawnProcess,
    useWorktrees: managed,
    log: (message) => console.error(message),
  });

  // A developer's own working tree is not this app's to rewire; only a checkout
  // the shell created (or one it was pointed at explicitly) counts.
  function requireManaged() {
    if (!managed) {
      throw new Error(
        "This checkout is not managed by the app. Use the packaged app, or point MAGI_DEV_CHECKOUT at a scratch checkout.",
      );
    }
  }

  /**
   * ASP is a local runtime owned by the App, not by the Electron shell. The
   * shell has already made the App worktree so it can load this module; from
   * here on the App owns this separate `magi/asp` worktree.
   */
  async function ensureAspWorktree() {
    if (!managed) return aspCheckout;
    const destination = path.join(paths.home, ".magi", "asp", "MAGI");
    if (existsSync(path.join(destination, ".git"))) {
      aspCheckout = destination;
      return aspCheckout;
    }
    if (existsSync(destination)) {
      throw new Error(`MAGI ASP worktree path is not a Git checkout: ${destination}`);
    }
    mkdirSync(path.dirname(destination), { recursive: true });
    await command(git.binary, ["worktree", "prune"], {
      ...options,
      description: "Could not prune stale MAGI worktrees",
    });
    let branchExists = true;
    try {
      await command(git.binary, ["show-ref", "--verify", "--quiet", "refs/heads/magi/asp"], {
        ...options,
        description: "Could not look up the ASP worktree branch",
      });
    } catch {
      branchExists = false;
    }
    await command(
      git.binary,
      branchExists
        ? ["worktree", "add", destination, "magi/asp"]
        : ["worktree", "add", "-b", "magi/asp", destination, "HEAD"],
      { ...options, description: "Could not create the ASP worktree" },
    );
    aspCheckout = destination;
    return aspCheckout;
  }

  const appData = path.join(paths.home, ".magi", "app");
  mkdirSync(appData, { recursive: true });
  function chatStore() {
    chatStorePromise ??= openChatStore(path.join(appData, "chat.sqlite"));
    return chatStorePromise;
  }
  const tokenPath = path.join(appData, "github-token");
  const metadataPath = path.join(appData, "github.json");
  const avatarPath = path.join(appData, "github-avatar");
  const providerPath = path.join(appData, "provider.json");
  if (existsSync(tokenPath)) chmodSync(tokenPath, 0o600);
  const tokenFile = () => tokenPath;
  const metadataFile = () => metadataPath;
  const avatarFile = () => avatarPath;

  async function updateRepository() {
    try {
      return (await gitText(["remote", "get-url", "origin"], "Could not read the update repository")) || repository;
    } catch {
      return repository;
    }
  }

  async function shellUpdateStatus() {
    if (shellUpdate === undefined) {
      return {
        packaged: false, currentVersion: "", latestVersion: "", latestTag: "", updateAvailable: false,
        assetName: "", assetUrl: "", releaseUrl: "", reason: "unavailable", error: "The desktop shell is not available.",
      };
    }
    return await shellRelease({
      repository: await updateRepository(),
      currentVersion: shellUpdate.currentVersion,
      packaged: shellUpdate.packaged,
      platform: process.platform,
      arch: process.arch,
    });
  }

  async function installShellUpdate() {
    if (shellUpdate === undefined || !shellUpdate.packaged) {
      throw new Error("A development shell cannot replace itself from a Release.");
    }
    const release = await shellUpdateStatus();
    if (!release.updateAvailable || release.assetUrl === "" || release.assetName === "") {
      throw new Error(release.error || "This client is already current.");
    }
    const updates = path.join(appData, "updates");
    mkdirSync(updates, { recursive: true });
    const installer = installerPath(updates, release.assetName);
    await downloadShellInstaller(release.assetUrl, installer);
    return await shellUpdate.install(installer);
  }
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
      ["provider", "model", "api_key", "base_url"].map((field) => [
        field,
        typeof settings?.[field] === "string" ? settings[field].trim() || null : null,
      ]),
    );
  }

  async function providerCatalog() {
    const entry = path.join(appCheckout, "magi", "node_modules", "@earendil-works", "pi-ai", "dist", "providers", "all.js");
    const { getBuiltinModels } = await import(pathToFileURL(entry).href);
    return Object.fromEntries(["openai", "anthropic", "minimax", "deepseek"].map((provider) => [
      provider, getBuiltinModels(provider).map((model) => ({ id: model.id, name: model.name })),
    ]));
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

  async function providerUsage() {
    const settings = readProvider();
    const provider = settings?.provider ?? null;
    if (!provider || !settings?.api_key) {
      return { provider, status: "unconfigured", balances: [], message: "" };
    }
    if (provider !== "deepseek") {
      return { provider, status: "unsupported", balances: [], message: "" };
    }
    try {
      const response = await fetch("https://api.deepseek.com/user/balance", {
        headers: { Authorization: `Bearer ${settings.api_key}` },
        signal: AbortSignal.timeout(8_000),
      });
      if (!response.ok) {
        return {
          provider,
          status: "error",
          balances: [],
          message: `DeepSeek balance request failed (${response.status})`,
        };
      }
      const body = await response.json();
      const balances = Array.isArray(body?.balance_infos)
        ? body.balance_infos.flatMap((entry) =>
            typeof entry?.currency === "string" && typeof entry?.total_balance === "string"
              ? [{ currency: entry.currency, total: entry.total_balance }]
              : [],
          )
        : [];
      return {
        provider,
        status: "available",
        available: body?.is_available === true,
        balances,
        message: "",
      };
    } catch (error) {
      return {
        provider,
        status: "error",
        balances: [],
        message: error instanceof Error ? error.message : String(error),
      };
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

  async function aspHealth() {
    try {
      const response = await fetch(healthUrl(), { signal: AbortSignal.timeout(400) });
      if (!response.ok) return "unavailable";
      const health = await response.json();
      return health?.status === "ok" && health?.runtime === "typescript"
        ? "ready"
        : "incompatible";
    } catch {
      return "unavailable";
    }
  }

  async function waitForAsp(timeoutMs = 20_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const health = await aspHealth();
      if (health === "ready") {
        return;
      }
      if (health === "incompatible") throw new Error(`An older ASP is already listening at ${ASP_ORIGIN.href}; quit the old MAGI service before retrying.`);
      await delay(200);
    }
    throw new Error(`ASP is unavailable at ${healthUrl()}`);
  }

  function aspNode() {
    // Packaged builds pass the bundled Node 24 binary. An unpackaged shell
    // borrows whatever `node` is on PATH, which may be too old to run the
    // TypeScript server, so prefer the checkout's prepared runtime.
    if (typeof tools.node === "string" && tools.node !== "node" && existsSync(tools.node)) {
      return tools.node;
    }
    const bundled = path.join(
      paths.checkout,
      "shell",
      "runtime",
      "bin",
      process.platform === "win32" ? "node.exe" : "node",
    );
    return existsSync(bundled) ? bundled : tools.node;
  }

  function spawnAsp() {
    const aspDir = path.join(aspCheckout, "asp");
    const child = spawnProcess(aspNode(), ["main.ts"], {
      cwd: aspDir,
      env: {
        ...tools.env,
        MAGI_ASP_HOST: ASP_ORIGIN.hostname,
        MAGI_ASP_PORT: ASP_ORIGIN.port || "42069",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.stderr?.on("data", (chunk) => {
      const text = String(chunk).trim();
      if (text) {
        console.error("[asp]", text);
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
    const built = path.join(appCheckout, "app", "dist", "index.html");
    return existsSync(built) ? built : "http://127.0.0.1:5173";
  }

  /** Rebuild the interface only when explicitly requested. */
  async function rebuildInterface() {
    const appDir = path.join(appCheckout, "app");
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
   * Everything this checkout needs before it can run. Only a managed checkout
   * is prepared: a developer's own tree is already theirs to prepare.
   */
  async function prepare(progress) {
    await ensureAspWorktree();
    const aspDir = path.join(aspCheckout, "asp");
    const appDir = path.join(appCheckout, "app");
    // The App owns the provider catalog it reads, so its worktree also carries
    // the small MAGI dependency tree. Agent-specific builds stay in their own
    // worktrees; the root checkout remains source-only.
    const magiDir = path.join(appCheckout, "magi");
    const required = [
      path.join(aspDir, "package-lock.json"),
      path.join(appDir, "package-lock.json"),
      path.join(magiDir, "bun.lock"),
    ];
    for (const file of required) {
      if (!existsSync(file)) {
        throw new Error(`MAGI checkout is incomplete: ${file} is missing`);
      }
    }

    if (managed && !existsSync(path.join(aspDir, "node_modules"))) {
      progress?.("Preparing local ASP…", 0.2);
      await command(tools.node, [tools.npm, "ci"], {
        cwd: aspDir,
        env: tools.env,
        description: "Could not prepare ASP",
      });
    }
    if (managed && !existsSync(path.join(magiDir, "node_modules"))) {
      progress?.("Preparing MAGI…", 0.4);
      await command(tools.bun, ["install", "--frozen-lockfile"], {
        cwd: magiDir,
        env: tools.env,
        description: "Could not prepare MAGI",
      });
    }
    if (managed && !existsSync(path.join(appDir, "node_modules"))) {
      progress?.("Installing app dependencies…", 0.6);
      await command(tools.node, [tools.npm, "ci"], {
        cwd: appDir,
        env: tools.env,
        description: "Could not install the desktop app dependencies",
      });
    }
    if (managed && !existsSync(path.join(appDir, "dist", "index.html"))) {
      progress?.("Building the app…", 0.8);
      await command(tools.node, [tools.npm, "run", "build"], {
        cwd: appDir,
        env: tools.env,
        description: "Could not build the desktop app",
      });
    }

    progress?.("Prepared.", 0.85);
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
    const previous = readProvider();
    writeProvider(settings);
    providerAttempted.clear();
    let result;
    try {
      const { token } = await aspJson("/operator");
      await ensureProviderRelay(token);
      result = await syncProvider(settings, null, token);
    } catch (error) {
      throw new Error(`Saved in the app, but MAGI sync failed: ${error instanceof Error ? error.message : String(error)}`);
    }
    if ((result.synced ?? []).length === 0 && (result.failed ?? []).length > 0 &&
        result.failed.every((item) => item.detail === "MAGI rejected provider configuration")) {
      if (previous === null) rmSync(providerPath, { force: true });
      else writeProvider(previous);
      throw new Error("MAGI rejected the provider configuration; the previous settings were kept");
    }
    for (const handle of result.synced ?? []) providerAttempted.add(handle);
    for (const item of result.failed ?? []) providerAttempted.add(item.handle);
    return result;
  }

  function watchProviderRecipients(token) {
    if (providerTimer !== null) return;
    const poll = async () => {
      try {
        const settings = readProvider();
        if (settings !== null) {
          const bots = (await aspJson("/bots", { token }))?.bots ?? [];
          const online = new Set(bots.filter((bot) => bot.online).map((bot) => bot.handle));
          for (const handle of providerAttempted) {
            if (!online.has(handle)) providerAttempted.delete(handle);
          }
          const pending = [...online].filter((handle) => !providerAttempted.has(handle));
          if (pending.length > 0) {
            const result = await syncProvider(settings, pending, token);
            for (const handle of result.synced ?? []) providerAttempted.add(handle);
            for (const item of result.failed ?? []) providerAttempted.add(item.handle);
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
    // This backend runs them, not ASP: start the new ones right away.
    await startManagedMagi();
    await Promise.all(
      created.map(({ handle, nickname }) =>
        nameWhenOnline(handle, nickname, token).catch((error) => {
          console.error(
            `[asp] ${error instanceof Error ? error.message : String(error)}`,
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
    await ensureAspWorktree();
    progress?.("Checking local ASP…", 0.9);
    const health = await aspHealth();
    if (health === "incompatible") throw new Error(`An older ASP is already listening at ${ASP_ORIGIN.href}; quit the old MAGI service before retrying.`);
    if (health === "ready") {
      // The society comes up once here; after that it is the operator's to
      // start and stop from each MAGI's profile.
      await startManagedMagi();
      await ensureDefaultMagis(progress);
      await activateProviderSync();
      return { origin: ASP_ORIGIN.href };
    }
    const aspDir = path.join(aspCheckout, "asp");
    if (!existsSync(path.join(aspDir, "main.ts"))) {
      throw new Error(`ASP was not found at ${aspDir}`);
    }
    progress?.("Starting local ASP…", 0.94);
    asp = spawnAsp();
    try {
      await new Promise((resolve, reject) => {
        asp.once("error", reject);
        asp.once("spawn", resolve);
      });
      progress?.("Waiting for local ASP…", 0.97);
      await Promise.race([
        waitForAsp(),
        new Promise((_, reject) => {
          asp.once("exit", (code, signal) => {
            reject(new Error(`ASP exited (${code ?? signal ?? "unknown"})`));
          });
        }),
      ]);
    } catch (error) {
      dispose();
      throw error;
    }
    await startManagedMagi();
    await ensureDefaultMagis(progress);
    await activateProviderSync();
    return { origin: ASP_ORIGIN.href };
  }

  function dispose() {
    if (chatStorePromise !== null) {
      void chatStorePromise.then((store) => store.close());
      chatStorePromise = null;
    }
    if (providerTimer !== null) clearTimeout(providerTimer);
    providerTimer = null;
  }

  // Renderer/backend reloads and runtime shutdown are deliberately separate.
  // A cache-busted backend instance can be retired while the ASP process (and
  // therefore its MAGI children) keeps serving the newly loaded interface.
  function shutdown() {
    dispose();
    magiRuntime.stopAll();
    if (asp && !asp.killed) {
      asp.kill("SIGTERM");
    }
    asp = null;
  }

  async function runtimeAction(action) {
    requireManaged();
    return await magiAction(action);
  }

  // Starting a MAGI is this backend's job even for a developer's own checkout:
  // only the operations that rewrite the checkout (rebuild, merge) need the app
  // to own it.
  async function magiAction(action) {
    if (runtimeBusy) throw new Error("Another runtime operation is still running.");
    runtimeBusy = true;
    try {
      return await action();
    } finally {
      runtimeBusy = false;
    }
  }

  /** The agents this machine is supposed to run, with their credentials. */
  async function magiRoster() {
    const { token } = await aspJson("/operator");
    const agents = (await aspJson("/agents", { token }))?.agents ?? [];
    return agents.filter(
      (agent) =>
        agent?.managed === true &&
        typeof agent.handle === "string" &&
        typeof agent.token === "string",
    );
  }

  async function magiAgent(payload) {
    const handle = typeof payload?.handle === "string" ? payload.handle : "";
    if (handle === "") throw new Error("A MAGI handle is required.");
    const agent = (await magiRoster()).find((row) => row.handle === handle);
    if (agent === undefined) throw new Error(`Unknown MAGI: ${handle}`);
    return agent;
  }

  /**
   * The society of this machine comes up once, when the app brings ASP up.
   * Afterwards nothing restarts behind the operator's back: the left panel
   * shows who is offline, and a MAGI's profile starts what it needs.
   */
  async function startManagedMagi() {
    try {
      if ((await aspHealth()) !== "ready") return { started: [], failed: [] };
      return await magiRuntime.startAll(await magiRoster());
    } catch (error) {
      console.error(
        `[magi] could not start the society: ${error instanceof Error ? error.message : String(error)}`,
      );
      return { started: [], failed: [] };
    }
  }

  /** What a MAGI's profile shows: whether it is up, and where it runs from. */
  async function magiInfo(payload) {
    const agent = await magiAgent(payload);
    const { token } = await aspJson("/operator");
    const agents = (await aspJson("/agents", { token }))?.agents ?? [];
    const row = agents.find((entry) => entry.handle === agent.handle);
    return {
      handle: agent.handle,
      online: row?.online === true,
      running: magiRuntime.running().includes(agent.handle),
      branch: managed ? agentBranch(agent.handle) : "",
      source: magiRuntime.sourceOf(agent.handle),
    };
  }

  async function magiStart(payload) {
    return await magiRuntime.start(await magiAgent(payload));
  }

  async function magiStop(payload) {
    const agent = await magiAgent(payload);
    return { handle: agent.handle, stopped: magiRuntime.stop(agent.handle) };
  }

  async function magiRestart(payload) {
    return await magiRuntime.restart(await magiAgent(payload));
  }

  async function magiRebuild(payload) {
    if ((await aspHealth()) !== "ready") throw new Error("Start ASP before rebuilding this MAGI.");
    return await magiRuntime.rebuild(await magiAgent(payload));
  }

  async function magiMerge(payload) {
    return await magiRuntime.merge(await magiAgent(payload));
  }

  async function stopOwnedAsp() {
    if ((await aspHealth()) !== "ready") return;
    if (asp && !asp.killed) {
      asp.kill("SIGTERM");
    } else {
      const { token } = await aspJson("/operator");
      await aspJson("/runtime/asp/stop", { token, method: "POST" });
    }
    for (let attempt = 0; attempt < 50; attempt++) {
      if ((await aspHealth()) === "unavailable") {
        asp = null;
        return;
      }
      await delay(200);
    }
    throw new Error("ASP did not stop; rebuild was cancelled.");
  }

  async function runtimeStatus() {
    const health = await aspHealth();
    let magiOnline = 0;
    if (health === "ready") {
      const { token } = await aspJson("/operator");
      const bots = (await aspJson("/bots", { token }))?.bots ?? [];
      magiOnline = bots.filter((bot) => bot.online).length;
    }
    return {
      asp: health,
      owned: Boolean(asp && !asp.killed),
      magiOnline,
      canInstallShellUpdate: shellUpdate?.packaged === true,
    };
  }

  async function rebuildAsp() {
    return runtimeAction(async () => {
      await stopOwnedAsp();
      await command(tools.node, [tools.npm, "ci"], {
        cwd: path.join(aspCheckout, "asp"), env: tools.env,
        description: "Could not install ASP dependencies",
      });
      await start();
      return await runtimeStatus();
    });
  }

  async function rebuildApp() {
    return runtimeAction(async () => {
      await rebuildInterface();
      emit("app.interface-updated", {});
      return { ui: uiEntry() };
    });
  }

  function installerExtension() {
    return process.platform === "darwin" ? ".dmg" : process.platform === "win32" ? ".exe" : ".AppImage";
  }

  /** Build from the App worktree without touching the live shell or services. */
  async function buildInstallerFiles() {
    const shellDir = path.join(appCheckout, "shell");
    if (!existsSync(path.join(shellDir, "package-lock.json"))) {
      throw new Error(`MAGI shell was not found at ${shellDir}`);
    }
    await command(tools.node, [tools.npm, "ci"], {
      cwd: shellDir,
      env: tools.env,
      description: "Could not install shell packaging dependencies",
    });
    await command(tools.node, [tools.npm, "run", "build"], {
      cwd: shellDir,
      env: tools.env,
      description: "Could not build the local installer",
    });
    const output = path.join(shellDir, "release");
    const extension = installerExtension();
    const candidates = readdirSync(output)
      .filter((name) => name.endsWith(extension) && !name.includes(".blockmap"))
      .sort((left, right) => statSync(path.join(output, right)).mtimeMs - statSync(path.join(output, left)).mtimeMs);
    const name = candidates.find((candidate) => candidate.includes(`-${process.arch}${extension}`)) ?? candidates[0];
    if (name === undefined) throw new Error(`The build did not create a ${extension} installer in ${output}`);
    return { output, installer: path.join(output, name) };
  }

  async function buildInstaller() {
    return await runtimeAction(buildInstallerFiles);
  }

  async function buildAndInstallInstaller() {
    return await runtimeAction(async () => {
      if (shellUpdate?.packaged !== true) {
        throw new Error("Install the new client from a packaged MAGI app.");
      }
      const built = await buildInstallerFiles();
      return { ...built, ...(await shellUpdate.install(built.installer)) };
    });
  }

  function isAgentic(url) {
    try {
      return /agenticsociety/i.test(parseGitHubSlug(url).owner);
    } catch {
      return false;
    }
  }

  async function listedRemotes() {
    try {
      const text = await command(git.binary, ["remote", "-v"], {
        ...options,
        description: "Could not list Git remotes",
      });
      const found = new Map();
      for (const line of text.split("\n")) {
        const match = /^(\S+)\s+(\S+)\s+\(fetch\)$/.exec(line.trim());
        if (match) found.set(match[1], match[2]);
      }
      return [...found.entries()].map(([name, url]) => ({ name, url }));
    } catch {
      return [];
    }
  }

  // The checkout's origin may be the operator's fork. The comparison the About
  // page shows is against AgenticSociety, preferring a remote that already
  // points there and otherwise the repository this build was given.
  async function agenticUrl(remotes) {
    const availableRemotes = remotes ?? (await listedRemotes());
    const matched = availableRemotes.find((remote) => isAgentic(remote.url));
    if (matched) return matched.url;
    return isAgentic(repository) ? repository : "";
  }

  function githubRepository(url) {
    try {
      const { owner, name } = parseGitHubSlug(url);
      return `${owner}/${name}`;
    } catch {
      return "";
    }
  }

  async function gitText(args, description, env = tools.env) {
    return (
      await command(git.binary, args, {
        cwd: paths.checkout,
        env,
        description,
      })
    ).trim();
  }

  async function sourceStatus() {
    const checkout = paths.checkout;
    const blank = {
      available: false,
      branch: "",
      commit: "",
      latestCommit: "",
      tag: "",
      repository: "",
      upstreamRepository: "",
      commitUrl: "",
      tagUrl: "",
      forkPoint: "",
      forkPointUrl: "",
      remote: "",
      remoteAhead: false,
      remoteChecked: false,
    };
    if (typeof checkout !== "string" || checkout === "" || !existsSync(path.join(checkout, ".git"))) {
      return blank;
    }
    let branch = "";
    let commit = "";
    let tag = "";
    try {
      branch = await gitText(["rev-parse", "--abbrev-ref", "HEAD"], "Could not read the current branch");
      commit = await gitText(["rev-parse", "HEAD"], "Could not read the current commit");
    } catch {
      return blank;
    }
    try {
      tag = await gitText(
        ["describe", "--tags", "--abbrev=0", "HEAD"],
        "Could not read the checkout release tag",
      );
    } catch {
      // A development checkout may not have a release tag yet.
    }
    const remotes = await listedRemotes();
    let originUrl = remotes.find((entry) => entry.name === "origin")?.url ?? "";
    try {
      originUrl = await gitText(
        ["config", "--get", "remote.origin.url"],
        "Could not read the checkout origin",
      );
    } catch {
      // Fall back to the URL reported by `git remote -v`.
    }
    const checkoutRepository = githubRepository(originUrl) || githubRepository(repository);
    const remote = await agenticUrl(remotes);
    const upstreamRepository = githubRepository(remote);
    let latestCommit = commit;
    if (originUrl && branch && branch !== "HEAD") {
      try {
        const originHead = await gitText(
          ["ls-remote", originUrl, `refs/heads/${branch}`],
          "Could not read the fork branch's latest commit",
          { ...tools.env, GIT_TERMINAL_PROMPT: "0" },
        );
        latestCommit = /^(\S+)/.exec(originHead)?.[1] ?? commit;
      } catch {
        // Keep the local commit as a useful fallback while offline.
      }
    }
    const local = {
      ...blank,
      available: true,
      branch,
      commit,
      latestCommit,
      tag,
      repository: checkoutRepository,
      upstreamRepository,
      commitUrl:
        checkoutRepository && latestCommit
          ? `https://github.com/${checkoutRepository}/commit/${latestCommit}`
          : "",
      tagUrl:
        upstreamRepository && tag
          ? `https://github.com/${upstreamRepository}/releases/tag/${encodeURIComponent(tag)}`
          : "",
      remote,
    };
    if (remote === "") return local;
    const probeEnv = { ...tools.env, GIT_TERMINAL_PROMPT: "0" };
    try {
      const head = await gitText(
        ["-c", "protocol.file.allow=always", "ls-remote", "--symref", remote, "HEAD"],
        "Could not read the AgenticSociety default branch",
        probeEnv,
      );
      const branchName = /^ref:\s+refs\/heads\/(\S+)\s+HEAD$/m.exec(head)?.[1] ?? "main";
      await command(
        git.binary,
        ["-c", "protocol.file.allow=always", "fetch", "--quiet", remote, `refs/heads/${branchName}`],
        { cwd: checkout, env: probeEnv, description: "Could not fetch AgenticSociety" },
      );
      const forkPoint = await gitText(
        ["merge-base", "HEAD", "FETCH_HEAD"],
        "Could not find where this checkout diverged from AgenticSociety",
      );
      const ahead = await gitText(
        ["rev-list", "--count", "HEAD..FETCH_HEAD"],
        "Could not compare this checkout with AgenticSociety",
      );
      return {
        ...local,
        forkPoint,
        forkPointUrl:
          upstreamRepository && forkPoint
            ? `https://github.com/${upstreamRepository}/commit/${forkPoint}`
            : "",
        remoteAhead: ahead !== "0",
        remoteChecked: true,
      };
    } catch {
      return local;
    }
  }

  return {
    prepare,
    start,
    dispose,
    shutdown,
    "runtime.status": runtimeStatus,
    "runtime.stopAsp": () => runtimeAction(stopOwnedAsp),
    "runtime.startAsp": () => runtimeAction(() => start()),
    "runtime.rebuildAsp": rebuildAsp,
    "magi.info": magiInfo,
    "magi.start": (payload) => magiAction(() => magiStart(payload)),
    "magi.stop": (payload) => magiAction(() => magiStop(payload)),
    "magi.restart": (payload) => magiAction(() => magiRestart(payload)),
    "magi.rebuild": (payload) => runtimeAction(() => magiRebuild(payload)),
    "magi.merge": (payload) => runtimeAction(() => magiMerge(payload)),
    "runtime.rebuildApp": rebuildApp,
    "runtime.buildInstaller": buildInstaller,
    "runtime.buildAndInstallInstaller": buildAndInstallInstaller,
    "shell.updateStatus": shellUpdateStatus,
    "shell.installUpdate": installShellUpdate,
    "github.state": currentState,
    "github.signIn": signIn,
    "github.connect": connect,
    "provider.settings": () => readProvider() ?? normalizeProvider(null),
    "provider.catalog": providerCatalog,
    "provider.usage": providerUsage,
    "provider.save": saveProvider,
    "source.status": sourceStatus,
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
