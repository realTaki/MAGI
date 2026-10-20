/**
 * The operator backend: GitHub, MAGI processes, EVA lifecycle, and the cache
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
 *   start(progress)          — make the built-in Shell EVA available.
 *   dispose()                — release reloadable backend resources only.
 *   shutdown()               — stop owned EVA processes when the desktop exits.
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
import { chmodSync, existsSync, mkdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  chat, messageDelivery, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, loadOrCreateIdentity,
  SHELL_MAGI_HANDLE,
} from "@magi/bus";

import { ensureManagedClone } from "./clone.js";
import { localEvaManagement } from "./local-evas.js";
import { agentBranch, agentName, agentSource, createMagiRuntime } from "./magi-runtime.js";
import { downloadShellInstaller, installerPath, shellRelease } from "./shell-update.js";
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
  const { paths, repository, tools, emit, openExternal, copy, shellUpdate, managed = false, sourceVerified = false, bus } = context;
  const command = context.command ?? localCommand;
  // checkout is fixed editable source authority; appCheckout is the running
  // revision (including its UI). Never sync or rebuild back into that revision.
  const appCheckout = paths.appCheckout ?? paths.checkout;
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
  const magiRuntime = createMagiRuntime({
    checkout: paths.checkout,
    home: paths.home,
    base: "",
    tools,
    run: command,
    spawnProcess,
    startProcess: context.startProcess,
    prepareDependencies: context.prepareDependencies,
    managed,
    repository,
    configureGit: (root) => configureClone(root),
    initialProcesses: context.initialProcesses,
    workspaceRoot: bus ? path.dirname(bus.workspace) : path.join(paths.home, ".magi"),
    log: (message) => console.error(message),
  });

  // A developer's own working tree is not this app's to rewire; only a checkout
  // the shell created (or one it was pointed at explicitly) counts.
  function requireManaged() {
    if (!managed) {
      throw new Error(
        "This operation requires an app-managed installation. A source launch only enables explicit source sync and build actions.",
      );
    }
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

  /**
   * Everything this checkout needs before it can run. Only a managed checkout
   * is prepared: a developer's own tree is already theirs to prepare.
   */
  async function prepare(progress?: (message: string, percent: number) => void) {
    const appDir = path.join(appCheckout, "apps", "shell");
    if (!existsSync(path.join(appCheckout, "package-lock.json"))) throw new Error("MAGI checkout is incomplete");
    if (managed && context.prepareDependencies) await context.prepareDependencies(appCheckout);
    if (managed && !existsSync(path.join(appDir, "dist", "index.html"))) {
      await command(tools.node, [tools.npm, "run", "build:ui"], { cwd: appDir, env: tools.env, description: "Could not build desktop UI" });
    }
    progress?.("Prepared.", 0.85);
    return { ui: uiEntry() };
  }

  async function start(progress?: (message: string, percent: number) => void) {
    ownerChat();
    progress?.("Shell EVA is ready.", 1);
    return { origin: bus?.settings.get("peer.reachableURL") ?? "" };
  }

  const management = localEvaManagement(bus, magiRuntime, context.peerTransport);

  function ownerChat() {
    if (!bus) throw new Error("Shell EVA is unavailable");
    const owner = bus.chats.forChannel("shell-owner", "owner");
    messageDelivery.setHomeChat(bus, owner.id);
    return owner;
  }

  function peerContacts() {
    if (!bus) return [];
    return [
      { id: "shell", handle: SHELL_MAGI_HANDLE, name: "Shell EVA", nickname: "Shell EVA", url: bus.settings.get("peer.reachableURL") ?? "", local: true, running: context.nodeControl?.running() ?? true },
      ...bus.contacts.list().filter(c => c.peer_url).map(c => {
        const local = localOwned(String(c.id));
        return {
          id: String(c.id), handle: local?.handle ?? c.name, name: c.name, nickname: c.nickname, url: c.peer_url,
          local: Boolean(local), running: Boolean(local && magiRuntime.running().includes(local.handle)),
        };
      }),
    ];
  }

  function conversation(id) {
    if (typeof id === "string" && id.startsWith("conversation:")) {
      const group = bus.groups.get(id.slice("conversation:".length));
      if (!group) throw new Error("Unknown group conversation");
      return bus.chats.get(group.chat_id);
    }
    if (id === "shell") return ownerChat();
    const contact = bus.contacts.get(Number(id));
    if (!contact || !contact.peer_id) throw new Error("Peer contact does not exist");
    return bus.chats.forPeer(contact.peer_id);
  }

  function localOwned(id) {
    if (id === "shell") return null;
    const stored = bus.settings.get(`local.eva.${Number(id)}`);
    return stored ? JSON.parse(stored) : null;
  }

  function peerConversations() {
    return bus.groups.list().map(group => ({ id: `conversation:${group.conversation_uuid}`, title: group.title,
      trust: group.trust, members: bus.groups.membership(group.conversation_uuid)?.payload?.members ?? [], local: false, group: true }));
  }

  async function peerEvents({ id }) {
    const local = localOwned(id);
    if (local) return await magiRuntime.invokeOwner(local.handle, "owner.events", {});
    if (typeof id === "string" && id.startsWith("conversation:")) {
      return bus.groups.history(id.slice("conversation:".length), 500).map(({ payload }) => ({
        id: payload.eventId, content: payload.content, role: payload.author === bus.settings.get("peer.principal") ? "user" : "assistant",
        sender: bus.contacts.list().find(c => c.peer_principal === payload.author)?.nickname ?? payload.author,
        created_at: "", lamport: payload.lamport,
      }));
    }
    const room = conversation(id);
    return bus.messages.list(room.id, 500).map(m => ({ id: m.id, content: m.content,
      role: (id === "shell" ? m.contact_id === SYSTEM_CONTACT_ID : m.contact_id === MAGI_CONTACT_ID) ? "user" : "assistant",
      sender: bus.contacts.get(m.contact_id)?.nickname ?? bus.contacts.get(m.contact_id)?.name,
      metadata: JSON.parse(bus.settings.get(`webapp.message.${m.id}`) ?? "null") ?? undefined, created_at: m.created_at,
    }));
  }

  async function peerSend({ id, content, metadata }) {
    if (metadata?.group_action !== undefined) {
      if (id !== "shell" || Object.keys(metadata).length !== 1) throw new Error("Group decisions require the local owner conversation");
      const action = metadata.group_action;
      if (action?.action === "accept" && typeof action.id === "string") return bus.groups.accept(action.id);
      if (action?.action === "create" && typeof action.title === "string" && Array.isArray(action.members)) {
        const members = action.members.map(id => {
          const contact = bus.contacts.get(Number(id));
          if (!contact || !contact.peer_principal || !contact.peer_url) throw new Error("Choose peer contacts");
          return { principal: contact.peer_principal, endpoint: contact.peer_url };
        });
        return bus.groups.create({ title: action.title, members });
      }
      throw new Error("Invalid group owner command");
    }
    if (typeof id === "string" && id.startsWith("conversation:")) {
      if (metadata) throw new Error("Groups accept ordinary chat only");
      const uuid = id.slice("conversation:".length);
      return bus.groups.append(uuid, content);
    }
    if (metadata?.local_eva_action !== undefined) {
      if (id !== "shell" || Object.keys(metadata).length !== 1) throw new Error("Lifecycle commands require the local owner conversation");
      const action = metadata.local_eva_action;
      if (!action || !["start", "stop"].includes(action.action) || Object.keys(action).some(k => !["action", "contact_id"].includes(k))) throw new Error("Invalid local lifecycle command");
      const owner = ownerChat();
      messageDelivery.record(bus, owner.id, `${action.action} local EVA ${action.contact_id}`, SYSTEM_CONTACT_ID);
      let result;
      if (action.contact_id === "shell") {
        if (!context.nodeControl) throw new Error("Shell lifecycle is unavailable");
        result = await context.nodeControl.setRunning(action.action === "start");
      } else result = await management.find(t => t.name === "local_eva_lifecycle").run(action);
      messageDelivery.record(bus, owner.id, typeof result === "string" ? result : JSON.stringify(result), MAGI_CONTACT_ID);
      return { accepted: true };
    }
    const local = localOwned(id);
    if (local) return await magiRuntime.invokeOwner(local.handle, "owner.chat", { content, metadata });
    const room = conversation(id);
    if (id === "shell") return chat(bus, room.id, SYSTEM_CONTACT_ID, { content, metadata }, "webapp");
    if (metadata) throw new Error("Owner-only structured actions are available in the Shell conversation");
    return { id: messageDelivery.send(bus, room.id, content, MAGI_CONTACT_ID, "webapp") };
  }

  async function peerProfile({ id }) {
    const local = localOwned(id);
    if (local) return await magiRuntime.invokeOwner(local.handle, "owner.profile", {});
    if (id !== "shell") return { ...peerContacts().find(c => c.id === id), owner: false };
    return { handle: SHELL_MAGI_HANDLE, owner: true, public_key: loadOrCreateIdentity(bus.workspace).publicKey,
      provider: bus.settings.get("provider.name") ?? "", model: bus.settings.get("provider.model") ?? "" };
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
    await magiRuntime.stopAll();
  }

  async function runtimeAction(action) {
    requireManaged();
    return await magiAction(action);
  }

  async function sourceAction(action) {
    if (!managed && !sourceVerified) throw new Error("No verified editable MAGI source checkout. Start Electron from the MAGI source tree or use the installed app.");
    return await magiAction(async () => {
      if (sourceVerified) {
        await assertSourceRoot(paths.checkout);
        const manifest = JSON.parse(readFileSync(path.join(paths.checkout, "package.json"), "utf8"));
        const shell = JSON.parse(readFileSync(path.join(paths.checkout, "apps/shell/package.json"), "utf8"));
        if (manifest.name !== "magi" || shell.name !== "@magi/shell") throw new Error("The verified MAGI source checkout has changed identity; restart from the intended source tree.");
      }
      return await action();
    });
  }

  // Starting a MAGI is this backend's job even for a developer's own checkout:
  // merging a managed checkout is the operation that needs the app to own it.
  // A node builds and switches its own revision.
  async function magiAction(action) {
    if (runtimeBusy) throw new Error("Another runtime operation is still running.");
    runtimeBusy = true;
    try {
      return await action();
    } finally {
      runtimeBusy = false;
    }
  }

  async function runtimeStatus() {
    return { shell: "ready", owned: true, magiOnline: 1 + magiRuntime.running().length,
      sourceCheckout: paths.checkout, runtimeCheckout: appCheckout };
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

  async function assertSourceRoot(root) {
    const top = (await command(git.binary, ["rev-parse", "--show-toplevel"], gitAt(root))).trim();
    const a = statSync(top, { bigint: true }), b = statSync(root, { bigint: true });
    const same = realpathSync(top) === realpathSync(root) || (a.isDirectory() && b.isDirectory() && a.ino !== 0n && a.ino === b.ino && a.dev === b.dev);
    if (!same || path.basename(path.dirname(realpathSync(root))) === "revisions") {
      throw new Error(`Not an editable source checkout root: ${root}. Runtime revisions cannot be synchronized.`);
    }
  }

  async function requireCleanSource(root) {
    await assertSourceRoot(root);
    for (const marker of ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"]) {
      const file = (await command(git.binary, ["rev-parse", "--git-path", marker], gitAt(root))).trim();
      if (existsSync(path.resolve(root, file))) throw new Error(`A Git operation is in progress in ${root}. Finish or abort it yourself before syncing; nothing was changed.`);
    }
    // Dirty-check only. `git status` otherwise rewrites `.git/index` stat cache
    // (racy-git) even when porcelain is unchanged, which mutates the user's
    // checkout and breaks index-preserving sync refusals.
    const dirty = (await command(git.binary, ["status", "--porcelain=v1", "--untracked-files=all"], {
      ...gitAt(root),
      env: { ...tools.env, GIT_OPTIONAL_LOCKS: "0" },
    })).trim();
    if (dirty) throw new Error(`Local changes in ${root}. Commit or stash them before syncing; staged, unstaged and untracked files were preserved.`);
    return await sourceBranch(root);
  }

  /** Only fast-forward. Never stash, reset, switch branches or abort a user's merge. */
  async function mergeInto(root, branch, from) {
    if (await requireCleanSource(root) !== branch) throw new Error("The source branch changed during sync; retry from the intended branch.");
    const before = (await command(git.binary, ["rev-parse", "HEAD"], gitAt(root))).trim();
    try {
      await command(git.binary, ["merge", "--ff-only", from], {
        cwd: root, env: tools.env, description: `Could not merge ${from} into ${branch}`,
      });
      return before !== (await command(git.binary, ["rev-parse", "HEAD"], gitAt(root))).trim();
    } catch (error) {
      throw new Error(`${branch} could not fast-forward from ${from}. Local commits and changes were preserved; resolve divergent history manually. ${describe(error)}`);
    }
  }

  /** Explicit main sync; origin/HEAD may be stale or point at another branch. */
  async function syncCheckout(root) {
    const branch = await requireCleanSource(root);
    await command(git.binary, ["fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main"], {
      ...gitAt(root),
      env: { ...tools.env, GIT_TERMINAL_PROMPT: "0" },
      description: "Could not fetch from origin",
    });
    const from = "refs/remotes/origin/main";
    await command(git.binary, ["rev-parse", "--verify", `${from}^{commit}`], gitAt(root));
    return { branch, from, merged: await mergeInto(root, branch, from) };
  }

  /**
   * Update the source checkout itself from its remote. Each module clone
   * fetches the same remote independently.
   */
  async function updateSource() {
    const branch = await requireCleanSource(paths.checkout);
    if ((await gitConfigValue("remote.origin.url")) === "") {
      throw new Error("The source checkout has no origin remote to update from.");
    }
    // Never let a missing credential turn into a prompt nobody can answer.
    await gitText(["fetch", "--no-tags", "origin", `+refs/heads/${branch}:refs/remotes/origin/${branch}`], `Could not fetch origin/${branch}`, {
      ...tools.env, GIT_TERMINAL_PROMPT: "0",
    });
    let behind = 0;
    try {
      behind = Number(
        await gitText(
          ["rev-list", "--count", `HEAD..refs/remotes/origin/${branch}`],
          `Could not compare with origin/${branch}`,
        ),
      ) || 0;
    } catch {
      throw new Error(`origin has no ${branch} branch to update from.`);
    }
    const merged = behind > 0 ? await mergeInto(paths.checkout, branch, `refs/remotes/origin/${branch}`) : false;
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
  async function syncModule(module, root) {
    try {
      const { merged } = await syncCheckout(root);
      return { synced: [{ module, merged }], failed: [] };
    } catch (error) {
      return { synced: [], failed: [{ module, detail: describe(error) }] };
    }
  }

  /** Always update editable source, even while the backend runs a revision. */
  function syncApp() {
    return syncModule("shell", paths.checkout);
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
    "runtime.syncApp": () => sourceAction(syncApp),
    localRuntime: magiRuntime,
    localManagement: management,
    "peer.contacts": peerContacts,
    "peer.conversations": peerConversations,
    "peer.events": peerEvents,
    "peer.send": peerSend,
    "peer.profile": peerProfile,
    "runtime.interface": () => ({ ui: uiEntry(), revision: process.env.MAGI_REVISION ?? "", checkout: appCheckout, entry: process.argv[1] }),
    "shell.updateStatus": shellUpdateStatus,
    "shell.installUpdate": installShellUpdate,
    "github.state": currentState,
    "github.signIn": signIn,
    "github.connect": connect,
    "source.status": sourceStatus,
    "source.update": () => sourceAction(updateSource),
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
