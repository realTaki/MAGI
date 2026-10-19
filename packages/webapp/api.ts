/**
 * The operator backend: GitHub, MAGI processes, ASP lifecycle, and the cache
 * tables BUS does not cover.
 *
 * Electron no longer loads `main/index.ts`. Shell is `node dist/shell.js`.
 * The shell talks to that process over NDJSON and forwards ``local:invoke``.
 *
 * The shell hands over a context (paths, Git, and small native helpers) and
 * gets back a table of methods. The shell calls the methods it knows about
 * and nothing else:
 *   prepare(progress)        — get this checkout ready and report the interface
 *                              entry point (a file path or a URL).
 *   start(progress)          — bring up local ASP, the service this app talks to.
 *   dispose()                — release reloadable backend resources only.
 *   shutdown()               — stop ASP when the desktop itself exits.
 *   github.state             — account, fork and whether the checkout is wired.
 *   github.signIn            — OAuth device flow; stores the token for Git to use.
 *   github.connect           — fork when the account has none, then point the
 *                              checkout at it.
 *   source.status            — branch, commit, and how this checkout sits
 *                              against the AgenticSociety remote.
 *   source.update            — fetch the remote and merge it into this checkout.
 *
 * ``progress`` is ``(message, percent)`` with an absolute 0..1 percentage.
 */

import { spawn } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  ASP_MAGI_HANDLE,
  ASP_MAGI_NICKNAME,
  EVA_MAGI_HANDLE,
  EVA_MAGI_NICKNAME,
  SHELL_MAGI_HANDLE,
  SHELL_MAGI_NICKNAME,
} from "@magi/bus";
import { ASP_ORIGIN } from "./asp-process.js";
import { ensureManagedClone } from "./clone.js";
import { agentBranch, agentSource, createMagiRuntime } from "./magi-runtime.js";
import { downloadShellInstaller, installerPath, shellRelease } from "./shell-update.js";
// Host MAGIs live in Shell (@eva-000 Balthasar) and ASP (@eva-001 Casper).
// Only Melchior (@eva-002) is a spawned eva.js child.
const DEFAULT_SOCIETY = [
  { handle: SHELL_MAGI_HANDLE, nickname: SHELL_MAGI_NICKNAME },
  { handle: ASP_MAGI_HANDLE, nickname: ASP_MAGI_NICKNAME },
  { handle: EVA_MAGI_HANDLE, nickname: EVA_MAGI_NICKNAME },
];
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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function localCommand(binary, args, { cwd, env, description }): Promise<string> {
  return new Promise<string>((resolve, reject) => {
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

type JsonRequestOptions = {
  token?: string;
  method?: string;
  body?: unknown;
  timeoutMs?: number;
};

type DeviceCode = {
  device_code: string;
  user_code: string;
  expires_in: number;
  interval?: number;
  error_description?: string;
  error?: string;
};

type OAuthToken = {
  access_token?: string;
  error_description?: string;
  error?: string;
};

async function githubApi(endpoint, { token, method = "GET", body }: JsonRequestOptions = {}) {
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
  const data = await response.json().catch(() => null) as DeviceCode | null;
  if (!response.ok || typeof data?.device_code !== "string") {
    const detail = data?.error_description ?? data?.error ?? response.statusText;
    throw new Error(`GitHub did not return a sign-in code (${detail}).`);
  }
  return data;
}

async function pollDeviceToken({ device_code, interval, expires_in }: DeviceCode) {
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
    const data = await response.json().catch(() => null) as OAuthToken | null;
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
  const { paths, repository, tools, emit, openExternal, copy, shellUpdate, managed = false, aspProcess, bus } = context;
  const command = context.command ?? localCommand;
  // Shell loads this backend from the Shell clone. Each app has its own clone
  // of MAGI_REPOSITORY_URL on branch magi/<name>.
  const appCheckout = paths.appCheckout ?? paths.checkout;
  let aspCheckout = paths.checkout;
  const spawnProcess = context.spawn ?? spawn;
  const git = { binary: tools.git, env: tools.env };
  const options = { cwd: paths.checkout, env: tools.env };
  const gitAt = (cwd) => ({ cwd, env: tools.env });
  let deviceFlowPending = false;
  let runtimeBusy = false;
  let chatStorePromise = null;
  let disposed = false;
  let disposal = null;
  let initialization = null;
  const lifetime = new AbortController();
  // ASP keeps the roll of agents; this backend prepares their source trees,
  // while Shell owns processes and commands through the host (see magi-runtime.ts).
  // A developer's own checkout is never
  // rewired, so MAGI then share it exactly as before.
  const magiRuntime = createMagiRuntime({
    checkout: paths.checkout,
    home: paths.home,
    base: ASP_ORIGIN.href,
    tools,
    run: command,
    spawnProcess,
    startProcess: context.startProcess,
    prepareDependencies: context.prepareDependencies,
    managed,
    repository,
    configureGit: (root) => configureClone(root),
    initialProcesses: context.initialProcesses,
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
   * Shell prepares this separate `magi/asp` clone. Electron remains the
   * process and command owner; creating a clone grants no lifecycle authority.
   */
  async function ensureAspCheckout() {
    if (!managed) return aspCheckout;
    const destination = path.join(paths.home, ".magi", "asp", "MAGI");
    await ensureManagedClone({
      run: (args, gitOptions) => command(git.binary, args, gitOptions),
      env: tools.env,
      url: repository,
      destination,
      branch: "magi/asp",
    });
    await configureClone(destination);
    aspCheckout = destination;
    return aspCheckout;
  }

  const magiDataRoot = path.join(paths.home, ".magi");
  const appData = path.join(magiDataRoot, "shell");
  mkdirSync(appData, { recursive: true });
  async function chatStore() {
    if (disposed) throw new Error("The Shell backend is disposed.");
    chatStorePromise ??= import("./chat-store.js")
      .then(({ openChatStore }) => openChatStore(path.join(appData, "chat.sqlite"), bus));
    return chatStorePromise;
  }
  const tokenPath = path.join(appData, "github-token");
  const metadataPath = path.join(appData, "github.json");
  const avatarPath = path.join(appData, "github-avatar");
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

  async function gitConfigValue(key, cwd = paths.checkout) {
    try {
      return (await command(git.binary, ["config", "--local", "--get", key], {
        ...gitAt(cwd),
        description: `Could not read the Git setting ${key}`,
      })).trim();
    } catch {
      return "";
    }
  }

  async function remoteUrl(name, cwd = paths.checkout) {
    try {
      return (await command(git.binary, ["remote", "get-url", name], {
        ...gitAt(cwd),
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

  async function assignGitIdentity(viewer, cwd = paths.checkout) {
    if (typeof viewer?.login !== "string" || viewer.login === "") {
      return;
    }
    const name =
      typeof viewer.name === "string" && viewer.name.trim() !== "" ? viewer.name.trim() : viewer.login;
    const email = Number.isInteger(viewer.id)
      ? `${viewer.id}+${viewer.login}@users.noreply.github.com`
      : "";
    if ((await gitConfigValue("user.name", cwd)) === "") {
      await command(git.binary, ["config", "--local", "user.name", name], {
        ...gitAt(cwd),
        description: "Could not set the Git commit identity",
      });
    }
    if (email !== "" && (await gitConfigValue("user.email", cwd)) === "") {
      await command(git.binary, ["config", "--local", "user.email", email], {
        ...gitAt(cwd),
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
  async function pointCheckoutAtFork(fork, upstream, viewer, cwd = paths.checkout) {
    const forkUrl = `https://github.com/${fork.full_name}.git`;
    const upstreamUrl = `https://github.com/${upstream.owner}/${upstream.name}.git`;

    if (cwd === paths.checkout) emit("github.remote", { remote: forkUrl });
    const originUrl = await remoteUrl("origin", cwd);
    await command(
      git.binary,
      originUrl === ""
        ? ["remote", "add", "origin", forkUrl]
        : ["remote", "set-url", "origin", forkUrl],
      { ...gitAt(cwd), description: "Could not point origin at your fork" },
    );
    const trackedUpstream = await remoteUrl("upstream", cwd);
    await command(
      git.binary,
      trackedUpstream === ""
        ? ["remote", "add", "upstream", upstreamUrl]
        : ["remote", "set-url", "upstream", upstreamUrl],
      { ...gitAt(cwd), description: "Could not configure the upstream remote" },
    );
    await command(git.binary, ["config", "--local", "--replace-all", "credential.helper", ""], {
      ...gitAt(cwd),
      description: "Could not reset the local Git credential helper",
    });
    await command(git.binary, ["config", "--local", "--add", "credential.helper", credentialHelperValue()], {
      ...gitAt(cwd),
      description: "Could not store the Git credential helper",
    });
    await assignGitIdentity(viewer, cwd);
  }

  async function configureClone(root) {
    if (!existsSync(path.join(root, ".git"))) return;
    const token = readToken();
    const metadata = readMetadata();
    if (token === null || typeof metadata.fork !== "string" || metadata.fork === "") return;
    const upstream = upstreamSlug();
    if (upstream === null) return;
    await pointCheckoutAtFork({ full_name: metadata.fork }, upstream, {
      login: metadata.login,
      name: metadata.name,
    }, root);
  }

  async function configureManagedClones(fork, upstream, viewer) {
    const roots = new Set([paths.checkout, appCheckout]);
    if (existsSync(path.join(aspCheckout, ".git"))) roots.add(aspCheckout);
    const aspDestination = path.join(paths.home, ".magi", "asp", "MAGI");
    if (existsSync(path.join(aspDestination, ".git"))) roots.add(aspDestination);
    try {
      for (const agent of await magiRoster()) {
        const root = agentSource(paths.home, agent.handle);
        if (existsSync(path.join(root, ".git"))) roots.add(root);
      }
    } catch {
      // ASP may not be up yet; existing MAGI clones are still retargeted below.
    }
    for (const root of roots) await pointCheckoutAtFork(fork, upstream, viewer, root);
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
    await configureManagedClones(fork, upstream, viewer);
    await rememberViewer(viewer, { fork: fork.full_name, connectedAt: Date.now() });
    emit("github.connected", { login: viewer.login, fork: fork.full_name });
    return await currentState();
  }

  // ---------------------------------------------------------------------
  // Preparing this checkout and running its local service
  // ---------------------------------------------------------------------

  /** Interface entry point: the dev server when one is configured, else the build. */
  function uiEntry() {
    const devUrl = (process.env.MAGI_APP_URL ?? "").trim();
    if (devUrl !== "") {
      return devUrl;
    }
    const built = path.join(appCheckout, "apps", "shell", "dist", "index.html");
    return existsSync(built) ? built : "http://127.0.0.1:5173";
  }

  /** Rebuild the interface only when explicitly requested. */
  async function rebuildInterface() {
    const appDir = path.join(appCheckout, "apps", "shell");
    const build = (description) =>
      command(tools.node, [tools.npm, "run", "build:ui"], {
        cwd: appDir,
        env: tools.env,
        description,
      });
    try {
      await build("Could not rebuild the interface");
    } catch (error) {
      // A pulled commit can bring a new lockfile: install once, then try again.
      try {
        if (context.prepareDependencies) await context.prepareDependencies(appCheckout);
        else await command(tools.node, [tools.npm, "ci", "--ignore-scripts"], {
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
  async function prepare(progress?: (message: string, percent: number) => void) {
    await ensureAspCheckout();
    const aspDir = path.join(aspCheckout, "apps", "asp");
    const appDir = path.join(appCheckout, "apps", "shell");
    // The MAGI runtime is a workspace now: its packages hang off the checkout root,
    // so the root carries their lockfile and their node_modules — the Shell clone
    // installs them for itself, while each MAGI's own clone installs its own.
    const magiDir = appCheckout;
    const required = [
      path.join(aspDir, "package-lock.json"),
      path.join(appDir, "package-lock.json"),
      path.join(magiDir, "package-lock.json"),
    ];
    for (const file of required) {
      if (!existsSync(file)) {
        throw new Error(`MAGI checkout is incomplete: ${file} is missing`);
      }
    }

    // The desktop ships no C++ toolchain. Lifecycle scripts stay off so these
    // installs cannot reach node-gyp.
    if (managed && context.prepareDependencies) {
      progress?.("Checking local ASP dependencies…", 0.2);
      await context.prepareDependencies(aspCheckout);
      await context.prepareDependencies(magiDir);
    }
    if (managed && !context.prepareDependencies && !existsSync(path.join(aspCheckout, "node_modules", "drizzle-orm"))) {
      progress?.("Preparing local ASP…", 0.2);
      await command(tools.node, [tools.npm, "ci", "--ignore-scripts"], {
        cwd: aspCheckout,
        env: tools.env,
        description: "Could not prepare ASP",
      });
    }
    if (managed && !existsSync(path.join(aspDir, "dist", "asp.js"))) {
      progress?.("Building local ASP…", 0.25);
      await command(tools.node, [tools.npm, "run", "build"], {
        cwd: aspCheckout,
        env: tools.env,
        description: "Could not build ASP",
      });
    }
    if (managed && !context.prepareDependencies && !existsSync(path.join(magiDir, "node_modules"))) {
      progress?.("Preparing MAGI…", 0.4);
      await command(tools.node, [tools.npm, "ci", "--ignore-scripts"], {
        cwd: magiDir,
        env: tools.env,
        description: "Could not prepare MAGI",
      });
    }
    if (managed && !context.prepareDependencies && !existsSync(path.join(appDir, "node_modules"))) {
      progress?.("Installing app dependencies…", 0.6);
      await command(tools.node, [tools.npm, "ci", "--ignore-scripts"], {
        cwd: appDir,
        env: tools.env,
        description: "Could not install the desktop app dependencies",
      });
    }
    if (managed && !existsSync(path.join(appDir, "dist", "index.html"))) {
      progress?.("Building the app…", 0.8);
      await command(tools.node, [tools.npm, "run", "build:ui"], {
        cwd: appDir,
        env: tools.env,
        description: "Could not build the desktop app",
      });
    }

    progress?.("Prepared.", 0.85);
    return { ui: uiEntry() };
  }

  async function aspJson(endpoint, { token, method = "GET", body, timeoutMs = 20_000 }: JsonRequestOptions = {}) {
    const response = await fetch(new URL(endpoint, ASP_ORIGIN), {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.any([AbortSignal.timeout(timeoutMs), lifetime.signal]),
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
      const error = Object.assign(new Error(
        `ASP ${method} ${endpoint} failed (${response.status}): ${data?.detail ?? response.statusText}`,
      ), { status: response.status });
      throw error;
    }
    return data;
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
   * Seeds Balthasar, Casper, and Melchior. Shell and ASP already host the first
   * two; this registers missing handles, opens a 1:1 with each, and spawns
   * Melchior. Naming is best effort: an unnamed MAGI is cosmetic, and failing
   * startup over it would not be.
   */
  async function ensureDefaultMagis(progress) {
    const { token } = await aspJson("/operator");
    const chats = (await aspJson("/chats", { token }))?.chats ?? [];
    const haveChat = new Map();
    for (const chat of chats) {
      if (chat?.kind !== "bot" || !Array.isArray(chat.agents) || chat.agents.length !== 1) continue;
      const handle = chat.agents[0];
      if (typeof handle !== "string" || typeof chat.chat_id !== "string") continue;
      if (!haveChat.has(handle)) haveChat.set(handle, chat.chat_id);
    }
    const created = [];
    for (const member of DEFAULT_SOCIETY) {
      let chatId = haveChat.get(member.handle);
      if (typeof chatId !== "string") {
        progress?.("Creating default MAGIs…", 0.98);
        const chat = await aspJson("/chats", {
          token,
          method: "POST",
          body: { kind: "bot", handle: member.handle },
        });
        chatId = chat?.chat_id;
        if (typeof chatId === "string") {
          created.push({ handle: member.handle, nickname: member.nickname, chatId });
        }
      }
    }
    await attachHostChannel(token);
    // Spawn only classic EVA children. Shell and ASP hosts are skipped in magi-runtime.
    await startManagedMagi();
    await Promise.all(
      created.map(async ({ handle, nickname, chatId }) => {
        try {
          await nameWhenOnline(handle, nickname, token);
          await greet(chatId, token);
        } catch (error) {
          console.error(
            `[asp] ${error instanceof Error ? error.message : String(error)}`,
          );
        }
      }),
    );
  }

  /** Balthasar speaks on ASP through AspWorker once these settings exist. */
  async function attachHostChannel(token) {
    if (!bus) return;
    const agents = (await aspJson("/agents", { token }))?.agents ?? [];
    const host = agents.find((row) => row?.handle === SHELL_MAGI_HANDLE);
    if (typeof host?.token !== "string" || host.token === "") return;
    bus.settings.set("asp.base", ASP_ORIGIN.href);
    bus.settings.set("asp.token", host.token);
    bus.board("ManageWorkerNotify").publish({ worker: "asp", action: "start" }, "webapp");
  }

  /**
   * The first thing said in a chat, in the operator's name.
   *
   * It answers two questions at once: whether the new MAGI answers at all, and where
   * the operator is, which is what the MAGI keeps as its own thread to report trouble
   * to. A MAGI without a provider still answers — with that complaint.
   */
  async function greet(chatId, token) {
    await aspJson(`/chats/${encodeURIComponent(chatId)}/messages`, {
      token,
      method: "POST",
      body: { content: "Hi" },
    });
  }

  /** Brings local ASP up unless something already answers on its port. */
  async function start(progress?: (message: string, percent: number) => void) {
    await ensureAspCheckout();
    try {
      await aspProcess.ensure(aspCheckout, progress);
    } catch (error) {
      await dispose();
      throw error;
    }
    // The society comes up once here; after that it is the operator's to
    // start and stop from each MAGI's profile.
    await startManagedMagi();
    await ensureDefaultMagis(progress);
    return { origin: ASP_ORIGIN.href };
  }

  async function dispose() {
    if (disposal !== null) return await disposal;
    disposed = true;
    lifetime.abort();
    const pendingStore = chatStorePromise;
    chatStorePromise = null;
    disposal = (async () => {
      // Initialization failures retain their ready error; no open store exists.
      const store = pendingStore === null ? null : await pendingStore.catch(() => null);
      store?.close();
    })();
    await disposal;
  }

  async function shutdown() {
    await dispose();
    magiRuntime.stopAll();
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
      if ((await aspProcess.probe()) !== "ready") return { started: [], failed: [] };
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
    if ((await aspProcess.probe()) !== "ready") throw new Error("Start ASP before rebuilding this MAGI.");
    return await magiRuntime.rebuild(await magiAgent(payload));
  }

  async function magiMerge(payload) {
    return await magiRuntime.merge(await magiAgent(payload));
  }

  /**
   * Roster-level actions, for when going through the profiles one by one is the
   * slow way. One MAGI failing is reported, never fatal: a sweep the operator
   * cannot read is worse than no sweep at all.
   */
  async function magiStartAll() {
    if ((await aspProcess.probe()) !== "ready") throw new Error("Start ASP before starting the MAGIs.");
    return await magiRuntime.startAll(await magiRoster());
  }

  function magiStopAll() {
    return magiRuntime.stopAll();
  }

  async function magiRebuildAll() {
    if ((await aspProcess.probe()) !== "ready") throw new Error("Start ASP before rebuilding the MAGIs.");
    const rebuilt = [];
    const failed = [];
    for (const agent of await magiRoster()) {
      try {
        await magiRuntime.rebuild(agent);
        rebuilt.push(agent.handle);
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        failed.push({ handle: agent.handle, detail });
        console.error(`[magi] ${agent.handle}: ${detail}`);
      }
    }
    return { rebuilt, failed };
  }

  async function stopOwnedAsp() {
    await aspProcess.stopOwned();
  }

  async function runtimeStatus() {
    const health = await aspProcess.probe();
    let magiOnline = 0;
    if (health === "ready") {
      const { token } = await aspJson("/operator");
      const bots = (await aspJson("/bots", { token }))?.bots ?? [];
      magiOnline = bots.filter((bot) => bot.online).length;
    }
    return {
      asp: health,
      owned: aspProcess.owned(),
      magiOnline,
      canInstallShellUpdate: shellUpdate?.packaged === true,
    };
  }

  /** Stop ASP, reinstall from its lockfile, bring it back — and the society with it. */
  async function rebuildAspBody() {
    await stopOwnedAsp();
    await command(tools.node, [tools.npm, "ci"], {
      cwd: path.join(aspCheckout, "apps", "asp"), env: tools.env,
      description: "Could not install ASP dependencies",
    });
    await start();
    return await runtimeStatus();
  }

  async function rebuildAsp() {
    return await runtimeAction(rebuildAspBody);
  }

  async function sourceBranch(cwd = paths.checkout) {
    const branch = (
      await command(git.binary, ["rev-parse", "--abbrev-ref", "HEAD"], {
        ...gitAt(cwd),
        description: "Could not read the checkout branch",
      })
    ).trim();
    if (branch === "HEAD") {
      throw new Error("The checkout is on a detached HEAD; check out a branch first.");
    }
    return branch;
  }

  async function originDefault(cwd) {
    return (
      await command(git.binary, ["rev-parse", "--abbrev-ref", "origin/HEAD"], {
        ...gitAt(cwd),
        env: { ...tools.env, GIT_TERMINAL_PROMPT: "0" },
        description: "Could not read the origin default branch",
      })
    ).trim();
  }

  /** Merge one ref into a checkout; a conflict is aborted, never left half-merged. */
  async function mergeInto(root, branch, from) {
    try {
      const output = await command(git.binary, ["merge", "--no-edit", from], {
        cwd: root, env: tools.env, description: `Could not merge ${from} into ${branch}`,
      });
      return !/already up to date/i.test(output);
    } catch {
      try {
        await command(git.binary, ["merge", "--abort"], {
          cwd: root, env: tools.env, description: "Could not abort a conflicted merge",
        });
      } catch {
        // Nothing to abort: the merge never started.
      }
      throw new Error(`${branch} could not merge ${from}; the merge was aborted.`);
    }
  }

  /** Fetch origin and merge its default branch into this clone. */
  async function syncCheckout(root, branch) {
    await command(git.binary, ["fetch", "--prune", "origin"], {
      ...gitAt(root),
      env: { ...tools.env, GIT_TERMINAL_PROMPT: "0" },
      description: "Could not fetch from origin",
    });
    const from = await originDefault(root);
    return { branch, from, merged: await mergeInto(root, branch, from) };
  }

  /**
   * Update the source checkout itself from its remote. Each module clone
   * fetches the same remote independently.
   */
  async function updateSource() {
    const branch = await sourceBranch();
    if ((await gitConfigValue("remote.origin.url")) === "") {
      throw new Error("The source checkout has no origin remote to update from.");
    }
    // Never let a missing credential turn into a prompt nobody can answer.
    await gitText(["fetch", "--prune", "origin"], "Could not fetch from origin", {
      ...tools.env, GIT_TERMINAL_PROMPT: "0",
    });
    let behind = 0;
    try {
      behind = Number(
        await gitText(
          ["rev-list", "--count", `HEAD..origin/${branch}`],
          `Could not compare with origin/${branch}`,
        ),
      ) || 0;
    } catch {
      throw new Error(`origin has no ${branch} branch to update from.`);
    }
    const merged = behind > 0 ? await mergeInto(paths.checkout, branch, `origin/${branch}`) : false;
    return { branch, behind, synced: [{ module: "source", merged }], failed: [] };
  }

  function describe(error) {
    return error instanceof Error ? error.message : String(error);
  }

  /**
   * Sync one module's clone from its origin. Each module is synced by
   * its own button: the operator decides what to move, and a conflict is answered
   * here instead of being buried in a sweep.
   */
  async function syncModule(module, root, branch) {
    try {
      const { merged } = await syncCheckout(root, branch);
      return { synced: [{ module, merged }], failed: [] };
    } catch (error) {
      return { synced: [], failed: [{ module, detail: describe(error) }] };
    }
  }

  /** The operator clone, on the branch the shell checks out for it. */
  function syncApp() {
    return syncModule("shell", appCheckout, "magi/shell");
  }

  /** The ASP clone, created here if this is the first time it is needed. */
  async function syncAsp() {
    await ensureAspCheckout();
    return await syncModule("asp", aspCheckout, "magi/asp");
  }

  /** Every MAGI's own clone; `merge` restarts one whose branch actually moved. */
  async function syncMagis() {
    const synced = [];
    const failed = [];
    for (const agent of await magiRoster()) {
      try {
        const { merged } = await magiRuntime.merge(agent);
        synced.push({ module: agent.handle, merged });
      } catch (error) {
        failed.push({ module: agent.handle, detail: describe(error) });
      }
    }
    return { synced, failed };
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

  /** Build the Electron installer from the Shell source checkout. */
  async function buildInstallerFiles() {
    const shellDir = path.join(paths.checkout, "apps", "shell");
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
    ready: () => { initialization ??= chatStore().then(() => undefined); return initialization; },
    prepare,
    resume: async () => undefined,
    start,
    dispose,
    shutdown,
    "runtime.status": runtimeStatus,
    "runtime.stopAsp": () => runtimeAction(stopOwnedAsp),
    "runtime.startAsp": () => runtimeAction(() => start()),
    "runtime.rebuildAsp": rebuildAsp,
    "runtime.syncApp": () => runtimeAction(syncApp),
    "runtime.syncAsp": () => runtimeAction(syncAsp),
    "magi.info": magiInfo,
    "magi.start": (payload) => magiAction(() => magiStart(payload)),
    "magi.stop": (payload) => magiAction(() => magiStop(payload)),
    "magi.restart": (payload) => magiAction(() => magiRestart(payload)),
    "magi.rebuild": (payload) => runtimeAction(() => magiRebuild(payload)),
    "magi.merge": (payload) => runtimeAction(() => magiMerge(payload)),
    "magi.startAll": () => magiAction(() => magiStartAll()),
    "magi.stopAll": () => magiAction(() => magiStopAll()),
    "magi.rebuildAll": () => runtimeAction(() => magiRebuildAll()),
    "magi.syncAll": () => runtimeAction(syncMagis),
    "runtime.rebuildApp": rebuildApp,
    "runtime.interface": () => ({ ui: uiEntry() }),
    "runtime.buildInstaller": buildInstaller,
    "runtime.buildAndInstallInstaller": buildAndInstallInstaller,
    "shell.updateStatus": shellUpdateStatus,
    "shell.installUpdate": installShellUpdate,
    "github.state": currentState,
    "github.signIn": signIn,
    "github.connect": connect,
    "source.status": sourceStatus,
    "source.update": () => runtimeAction(updateSource),
    "chat.listChats": async () => (await chatStore()).listChats(),
    "chat.saveChats": async (rows) => (await chatStore()).saveChats(rows),
    "chat.listEvents": async (id) => (await chatStore()).listEvents(id),
    "chat.saveEvents": async ({ id, events }) => (await chatStore()).saveEvents(id, events),
    "chat.lastSequence": async (id) => (await chatStore()).lastSequence(id),
    "chat.pendingAcks": async (id) => (await chatStore()).pendingAcks(id),
    "chat.markAcknowledged": async ({ id, sequence }) =>
      (await chatStore()).markAcknowledged(id, sequence),
    "chat.queueOutgoingMessage": async (message) => (await chatStore()).queueOutgoingMessage(message),
    "chat.listOutgoingMessages": async () => (await chatStore()).listOutgoingMessages(),
    "chat.removeOutgoingMessage": async (id) => (await chatStore()).removeOutgoingMessage(id),
  };
}
