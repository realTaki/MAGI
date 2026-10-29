import { spawn } from 'node:child_process';
import { constants, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mkdir, realpath, lstat, readFile, writeFile, open, unlink, access, readdir } from 'node:fs/promises';
import { basename, dirname, join, resolve, relative, isAbsolute } from 'node:path';
import { ensureWorkspace } from './relocate-workspace.js';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { randomUUID, createHash } from 'node:crypto';
import { safePreview } from './message-preview.js';
import { CHILD_HOST_VERSION, DEFAULT_EVA_PRESET, resolveDmEntry, resolveMemoryEntry, resolveBotModeEntry, resolveSubconsciousEntry, readEvaPreset, desktopRoleExclusions, createChildHostPatch } from './child-host.js';

const here = dirname(fileURLToPath(import.meta.url));
const installationId = createHash('sha256').update(here).digest('hex').slice(0, 16);
const START_TIMEOUT = 60_000;
const STOP_TIMEOUT = 8_000;
const CHILD_NODE_CONFIG = JSON.stringify({ role: 'eva-child' }) + '\n';

// A home-level marker survives profile changes and later plugin installation.
export function isEvaChild(home) {
  let config;
  try { config = JSON.parse(readFileSync(join(home, 'eva', 'node.json'), 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
  if (config?.role !== 'eva-child') throw new Error('Invalid EVA node role in eva/node.json.');
  return true;
}

export function validateName(name) {
  if (typeof name !== 'string' || !/^[a-z][a-z0-9-]{0,31}$/.test(name)) throw new Error('Node name must match [a-z][a-z0-9-]{0,31}.');
  if (name === 'user') throw new Error('Node handle user is reserved for the local human DM address.');
  return name;
}

export function desktopRuntime(installAnchor, executable = process.execPath) {
  if (!installAnchor) throw new Error('eva requires the current DSH profileContext.installAnchor.');
  const cli = createRequire(installAnchor).resolve('@deepseek-ai/dsh-desktop-host/lib/cli.js');
  const runtimeDir = resolve(dirname(cli), '../../../..');
  const parent = dirname(runtimeDir);
  const resources = basename(parent) === 'app.asar' ? dirname(parent) : parent;
  return { executable, cli, runtimeDir, supportDir: join(resources, 'runtime') };
}

// Never follow a pre-existing symlink out of the selected workspace.
async function directories(base, parts) {
  let path = base;
  for (const part of parts) {
    path = join(path, part);
    try { await mkdir(path, { mode: 0o700 }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
    const stat = await lstat(path);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error(`Not an owned directory (symlinks are refused): ${path}`);
    const actual = await realpath(path);
    const delta = relative(base, actual);
    if (delta.startsWith('..') || isAbsolute(delta)) throw new Error(`Directory escapes the workspace: ${path}`);
  }
  return path;
}

async function exclusiveContent(path, text) {
  try { await writeFile(path, text, { flag: 'wx', mode: 0o600 }); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const stat = await lstat(path);
    if (!stat.isFile() || stat.isSymbolicLink() || await readFile(path, 'utf8') !== text) {
      throw new Error(`eva refuses to overwrite an existing or modified runtime file: ${path}`);
    }
  }
}

export function redact(text) {
  // Defense in depth only. Raw/filtered child output is never part of tool results.
  return text
    .replace(/https?:\/\/[^\s\u001b]+/g, (value) => {
      try { return new URL(value).origin + '/[URL details withheld]'; }
      catch { return '[URL redacted]'; }
    })
    .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi, '$1 [redacted]')
    .replace(/(["']?(?:access_token|refresh_token|api[_-]?key|token|authorization|password)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+)/gi, '$1"[redacted]"');
}

function isAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { return error.code !== 'ESRCH'; }
}

async function lockNode(path) {
  const token = randomUUID();
  const text = JSON.stringify({ ownerPid: process.pid, token });
  let handle;
  try { handle = await open(path, 'wx', 0o600); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    // Never infer ownership or send a signal from an on-disk PID.
    const owner = JSON.parse(await readFile(path, 'utf8'));
    throw new Error(`Node is locked by controller PID ${owner.ownerPid} (${isAlive(owner.ownerPid) ? 'alive' : 'stale'}). Inspect ${path} before removing the lock manually.`);
  }
  try { await handle.writeFile(text); } finally { await handle.close(); }
  return async () => {
    if (await readFile(path, 'utf8').catch(() => '') === text) await unlink(path);
  };
}

// Inventory reads only fixed, bounded metadata files, never profiles or credentials.
// Missing, unsafe, or malformed entries are not evidence of an EVA node.
async function inventoryDirectory(path) {
  try {
    const stat = await lstat(path);
    return stat.isDirectory() && !stat.isSymbolicLink() && await realpath(path) === path;
  } catch { return false; }
}

async function inventoryRecord(path) {
  let file;
  try {
    const stat = await lstat(path);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 4096 ||
        (process.getuid && stat.uid !== process.getuid())) return undefined;
    file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    const opened = await file.stat();
    if (!opened.isFile() || opened.ino !== stat.ino || opened.dev !== stat.dev || opened.size > 4096) return undefined;
    return JSON.parse(await file.readFile('utf8'));
  } catch { return undefined; }
  finally { await file?.close(); }
}

export class EvaRuntime {
  constructor(runtime, { startupTimeout = START_TIMEOUT, stopTimeout = STOP_TIMEOUT, homeRoot = homedir(), credentialsPath, dmAttach } = {}) {
    this.runtime = runtime;
    this.credentialsPath = credentialsPath;
    this.dmAttach = dmAttach;
    this.startupTimeout = startupTimeout;
    this.stopTimeout = stopTimeout;
    this.homeRoot = resolve(homeRoot);
    this.nodes = new Map();
    this.pending = new Map();
    this.disposed = false;
  }

  // Rebind a separately reloadable DM plugin without restarting owned Hosts.
  // The process object remains the authority for each peer's fixed identity.
  bindDm(attach) {
    if (this.disposed || this.dmBinding || typeof attach !== 'function') throw new Error('Duplicate, invalid or disposed DM broker binding.');
    const previous = this.dmAttach;
    const binding = Symbol('dm-broker');
    this.dmBinding = binding;
    this.dmAttach = attach;
    const release = () => {
      if (this.dmBinding !== binding) return;
      this.dmBinding = undefined;
      this.dmAttach = previous;
      for (const node of this.nodes.values()) {
        node.dmClose?.();
        node.dmClose = undefined;
      }
    };
    try {
      for (const node of this.nodes.values()) {
        if (node.finished || !node.child?.connected) continue;
        node.dmClose?.();
        node.dmClose = undefined;
        node.dmClose = attach(node.child, node.name);
      }
    } catch (error) { release(); throw error; }
    return release;
  }

  paths(handle) {
    validateName(handle);
    const home = join(this.homeRoot, `.dsh-${handle}`);
    const state = join(home, 'eva');
    return { handle, home, state, lock: join(state, 'owner.lock'), profile: handle,
      profileDir: join(home, 'profiles', handle), workspace: home };
  }

  // Node identity is user-wide, not tied to the calling Session or workspace.
  async key(_base, handle) { return this.paths(handle).home; }

  async allocateHandle(signal) {
    if (await realpath(this.homeRoot) !== this.homeRoot) throw new Error('EVA home root must be canonical.');
    const occupied = new Set(await readdir(this.homeRoot));
    for (let number = 0; number <= 999999; number++) {
      signal?.throwIfAborted();
      const handle = `eva-${String(number).padStart(3, '0')}`;
      const directory = `.dsh-${handle}`;
      if (occupied.has(directory)) continue;
      // The root alone reserves the handle atomically against racing controllers.
      // Workspace initialization happens at start, under the node owner lock.
      try {
        await mkdir(join(this.homeRoot, directory), { mode: 0o700 });
        return handle;
      } catch (error) { if (error.code !== 'EEXIST') throw error; }
    }
    throw new Error('No free EVA numbered handle is available.');
  }

  snapshot(node) {
    return {
      handle: node.name, name: node.name, status: node.status, pid: node.child?.pid ?? null,
      profile: node.profile, profileDir: node.profileDir,
      workspace: node.workspace, home: node.home, cwd: node.ready?.cwd ?? null,
      defaultAgentPreset: node.ready?.defaultAgentPreset ?? null,
      codexAvailable: node.ready?.codexAvailable ?? null,
      origin: node.ready?.origin ?? null, workspaceId: node.ready?.workspaceId ?? null,
      mainSessionId: node.ready?.mainSessionId ?? null,
      preview: safePreview(node.preview),
      exitCode: node.exitCode ?? null, exitSignal: node.exitSignal ?? null,
      credentials: this.credentialsPath
        ? 'Local startup shares the parent credential store, including sign-in/out, account changes and saved API keys. Existing child credentials are preserved, not merged.'
        : 'Parent credentials are not copied; existing child data is preserved.',
    };
  }

  // User navigation only. The credential-bearing URL must never enter snapshot().
  launchUrl(handle) {
    const node = this.nodes.get(this.paths(handle).home);
    return node?.status === 'running' ? node.ready.launchUrl : undefined;
  }

  async discoverNode(handle) {
    const paths = this.paths(handle);
    if (!await inventoryDirectory(paths.home) || !await inventoryDirectory(paths.state)) return undefined;
    const marker = await inventoryRecord(join(paths.state, 'node.json'));
    if (marker?.role !== 'eva-child') {
      const mapping = await inventoryRecord(join(paths.state, 'main-session.json'));
      if (![1, 2].includes(mapping?.version) || mapping.profile !== handle ||
          typeof mapping.sessionId !== 'string' || !/^session-[A-Za-z0-9-]{1,160}$/.test(mapping.sessionId)) return undefined;
    }
    // A persisted identity says nothing about external process liveness. Do not
    // retain pseudo-processes or read a lock, launch URL, or Session storage.
    return this.snapshot({ ...paths, name: handle, status: 'not-started' });
  }

  async status(base, name) {
    if (name !== undefined) {
      const key = await this.key(base, name);
      let node = this.nodes.get(key);
      if (node) return this.snapshot(node);
      const discovered = await inventoryDirectory(this.homeRoot) ? await this.discoverNode(name) : undefined;
      node = this.nodes.get(key); // A concurrent start remains authoritative.
      return node ? this.snapshot(node) : discovered ?? { handle: name, name, status: 'not-started' };
    }
    const discovered = new Map();
    if (await inventoryDirectory(this.homeRoot)) {
      let entries = [];
      try { entries = await readdir(this.homeRoot, { withFileTypes: true }); } catch { /* Inventory is best-effort. */ }
      for (const entry of entries) {
        if (!entry.isDirectory() || !/^\.dsh-[a-z][a-z0-9-]{0,31}$/.test(entry.name)) continue;
        const handle = entry.name.slice(5);
        if (this.nodes.has(this.paths(handle).home)) continue;
        const snapshot = await this.discoverNode(handle);
        if (snapshot) discovered.set(handle, snapshot);
      }
    }
    for (const node of this.nodes.values()) discovered.set(node.name, this.snapshot(node));
    return { nodes: [...discovered.values()].sort((a, b) => a.handle < b.handle ? -1 : a.handle > b.handle ? 1 : 0) };
  }

  async start(base, name, signal) {
    signal?.throwIfAborted();
    if (this.disposed) throw new Error('eva is unloading.');
    const credentialsPath = typeof this.credentialsPath === 'function'
      ? await this.credentialsPath() : this.credentialsPath;
    if (credentialsPath !== undefined && (typeof credentialsPath !== 'string' || !isAbsolute(credentialsPath))) {
      throw new Error('EVA sharing requires an absolute credential store path.');
    }
    signal?.throwIfAborted();
    if (this.disposed) throw new Error('eva is unloading.');
    if (name === undefined) name = await this.allocateHandle(signal);
    const key = await this.key(base, name);
    if (this.pending.has(key)) return this.pending.get(key);
    const existing = this.nodes.get(key);
    if (existing && ['starting', 'running', 'stopping'].includes(existing.status)) {
      if (existing.status === 'running') return this.snapshot(existing);
      throw new Error(`Node is ${existing.status}; wait before starting it again.`);
    }
    const operation = this.startNode(base, name, key, signal, credentialsPath);
    this.pending.set(key, operation);
    try { return await operation; } finally { this.pending.delete(key); }
  }

  async startNode(base, name, key, signal, credentialsPath) {
    const hostHome = await realpath(this.homeRoot);
    if (hostHome !== this.homeRoot) throw new Error('EVA home root must be canonical.');
    const home = await directories(hostHome, [`.dsh-${validateName(name)}`]);
    const state = await directories(home, ['eva']);
    const release = await lockNode(join(state, 'owner.lock'));
    let node;
    try {
      const migrationPending = await lstat(join(state, 'migration.pending.json')).then(() => true, (error) => { if (error.code === 'ENOENT') return false; throw error; });
      if (migrationPending) throw new Error('EVA has an incomplete migration; inspect migration.pending.json before starting.');
      // Persist the child role before any plugins can load in the spawned Host.
      await exclusiveContent(join(state, 'node.json'), CHILD_NODE_CONFIG);
      const workspace = await ensureWorkspace({ home, handle: name, rootWorkspace: true });
      const profile = name;
      const profileDir = await directories(home, ['profiles', profile]);
      // Native child-plugin imports must originate inside the profiles tree.
      // Keep the shipped declaration outside the resettable profile itself:
      // official first-run initialization atomically replaces that whole dir.
      // Neither a default change nor a source update rewrites older artifacts.
      const preset = await readEvaPreset();
      const assets = await directories(home, ['profiles', '.eva-assets', profile]);
      const presetPath = join(assets, `preset-eva-${preset.digest}.yml`);
      await exclusiveContent(presetPath, preset.text);
      const desktopExclusions = await desktopRoleExclusions(profileDir);
      const defaultAgentPreset = DEFAULT_EVA_PRESET; // Not a permission mode.
      const sharing = credentialsPath === undefined ? {} : { credentialsPath, credentialSharingVersion: 1 };
      const dmEntry = this.runtime.dmEntry ?? resolveDmEntry();
      const memoryEntry = this.runtime.memoryEntry ?? resolveMemoryEntry();
      const botModeEntry = this.runtime.botModeEntry ?? resolveBotModeEntry();
      const subconsciousEntry = this.runtime.subconsciousEntry ?? resolveSubconsciousEntry();
      const startupConfigId = createHash('sha256').update(JSON.stringify({ workspace, profile, defaultAgentPreset, providerDefaultsVersion: 1, dmProtocolVersion: 1, childHostVersion: CHILD_HOST_VERSION, dmEntry, memoryEntry, botModeEntry, subconsciousEntry, evaPresetHash: preset.digest, desktopExclusions, ...sharing })).digest('hex').slice(0, 16);
      const patch = join(state, `startup-${installationId}-${startupConfigId}.patch.yml`);
      const overlay = createChildHostPatch({ workspace, profile, presetPath, credentialsPath, desktopExclusions, dmEntry, memoryEntry, botModeEntry, subconsciousEntry });
      await exclusiveContent(patch, JSON.stringify(overlay, null, 2) + '\n');
      const args = ['--profile', profile];
      // Initialize through the official CLI, and never reset a pre-existing profile.
      try { await access(join(profileDir, 'package.json')); }
      catch (error) {
        if (error.code !== 'ENOENT') throw error;
        if ((await readdir(profileDir)).length) throw new Error('Refusing to initialize a nonempty profile directory without package.json.');
        args.push('--from-default-profile', 'web');
      }
      args.push('--patch', patch, '--host', '127.0.0.1', '--port', '0', '--no-open');
      signal?.throwIfAborted();
      if (this.disposed) throw new Error('eva is unloading.');
      const env = { ...process.env, DSH_HOME: home, DSH_TELEMETRY_DISABLED: '1', ELECTRON_RUN_AS_NODE: '1' };
      // Do not inherit another Host's runtime address or API-key environment grants.
      delete env.DSH_WEB_URL;
      for (const key of Object.keys(env)) if (/(?:API_?KEY|ACCESS_TOKEN|REFRESH_TOKEN|AUTH_TOKEN)$/i.test(key)) delete env[key];
      const child = spawn(this.runtime.executable, [
        '--expose-internals', join(here, 'runner.js'),
        this.runtime.cli, this.runtime.runtimeDir, this.runtime.supportDir, ...args,
      ], {
        cwd: home, env, stdio: ['ignore', 'pipe', 'pipe', 'ipc'], windowsHide: true,
        // A process group is for tree cleanup, not a detached service: IPC owns lifetime.
        detached: process.platform !== 'win32',
      });
      node = { name, child, profile, profileDir, home, workspace, status: 'starting', diagnostics: '' };
      this.nodes.set(key, node);
      let resolveExit;
      node.exited = new Promise((done) => { resolveExit = done; });
      // Use process exit, not stdio close: descendants can inherit and hold output pipes.
      child.on('error', (error) => { node.spawnError = error; });
      child.on('message', (message) => {
        if (node.status !== 'running' || message?.type !== 'eva-preview' ||
            message.mainSessionId !== node.ready?.mainSessionId) return;
        node.preview = safePreview(message.preview);
      });
      const finish = async (code, exitSignal) => {
        if (node.finished) return;
        node.finished = true;
        node.dmClose?.();
        node.dmClose = undefined;
        node.exitCode = code; node.exitSignal = exitSignal;
        node.status = code === 0 || node.stopping ? 'stopped' : 'failed';
        this.signalNode(node, 'SIGKILL');
        child.stdout.destroy(); child.stderr.destroy();
        try { await release(); } catch (error) { node.diagnostics += `\nLock cleanup: ${error.message}`; }
        resolveExit();
      };
      child.once('exit', finish);
      child.once('close', finish); // Spawn failures can have close without exit.
      for (const stream of [child.stdout, child.stderr]) {
        stream.setEncoding('utf8');
        // Keep only bounded, redacted diagnostic lines; never return a login token.
        let partial = '';
        stream.on('data', (chunk) => {
          partial += chunk;
          const lines = partial.split('\n'); partial = lines.pop().slice(-4096);
          for (const line of lines) node.diagnostics = (node.diagnostics + redact(line) + '\n').slice(-8192);
        });
      }
      try {
        node.dmClose = this.dmAttach?.(child, name);
        await new Promise((done, fail) => {
          const cleanup = () => { clearTimeout(timer); child.off('message', onMessage); child.off('error', onError); child.off('close', onClose); signal?.removeEventListener('abort', onAbort); };
          const settle = (error) => { cleanup(); error ? fail(error) : done(); };
          const onError = (error) => settle(error);
          const onClose = () => settle(new Error(`Child exited before readiness. Startup output is withheld to avoid exposing credentials.`));
          const onAbort = () => settle(signal.reason ?? new Error('eva start cancelled.'));
          const onMessage = (message) => {
            if (message?.type !== 'eva-ready') return;
            if (message.pid !== child.pid || message.profile !== profile || message.workspace !== workspace || message.home !== home || message.cwd !== home || message.profileDir !== profileDir || typeof message.defaultAgentPreset !== 'string' || !message.defaultAgentPreset) {
              settle(new Error('Child readiness facts do not match the requested isolation.')); return;
            }
            if (credentialsPath !== undefined && message.credentialsShared !== true) {
              settle(new Error('Child did not verify the shared credential store.')); return;
            }
            if (message.codexAvailable !== true) {
              settle(new Error('Child did not verify the native Codex provider.')); return;
            }
            let url;
            try { url = new URL(message.origin); } catch { settle(new Error('Invalid child origin.')); return; }
            if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1') { settle(new Error('Child is not loopback-only.')); return; }
            let launch;
            try { launch = new URL(message.launchUrl); } catch { settle(new Error('Invalid child navigation readiness.')); return; }
            if (launch.origin !== url.origin || launch.pathname !== '/' || launch.username || launch.password ||
                launch.hash !== '#eva-main' || typeof message.mainSessionId !== 'string' || !message.mainSessionId) {
              settle(new Error('Invalid child navigation readiness.')); return;
            }
            node.ready = message;
            node.status = 'running';
            settle();
          };
          const timer = setTimeout(() => settle(new Error(`Child startup timed out. Startup output is withheld to avoid exposing credentials.`)), this.startupTimeout);
          child.on('message', onMessage); child.once('error', onError); child.once('close', onClose);
          signal?.addEventListener('abort', onAbort, { once: true });
          if (signal?.aborted) onAbort();
        });
      } catch (error) { await this.stopNode(node); throw error; }
      return this.snapshot(node);
    } catch (error) {
      if (!node) await release();
      throw error;
    }
  }

  signalNode(node, signal) {
    if (!node.child.pid) return;
    try {
      if (process.platform !== 'win32') process.kill(-node.child.pid, signal);
      else node.child.kill(signal);
    } catch (error) {
      if (error.code !== 'ESRCH') node.diagnostics += `\nProcess signal failed: ${error.code}`;
    }
  }

  async stopNode(node) {
    if (node.status === 'stopped' || node.status === 'failed') { await node.exited; return this.snapshot(node); }
    node.stopping = true;
    node.status = 'stopping';
    this.signalNode(node, 'SIGTERM');
    const timer = setTimeout(() => this.signalNode(node, 'SIGKILL'), this.stopTimeout);
    try { await node.exited; } finally { clearTimeout(timer); }
    return this.snapshot(node);
  }

  async stop(base, name) {
    if (name === undefined) throw new Error('EVA stop requires a handle.');
    const key = await this.key(base, name);
    // Starting and stopping may arrive from different Sessions.
    await this.pending.get(key)?.catch(() => {});
    const node = this.nodes.get(key);
    return node ? this.stopNode(node) : { handle: name, name, status: 'not-started' };
  }

  async dispose() {
    this.disposed = true;
    await Promise.allSettled([...this.nodes.values()].map((node) => this.stopNode(node)));
    await Promise.allSettled([...this.pending.values()]);
    await Promise.allSettled([...this.nodes.values()].map((node) => this.stopNode(node)));
  }
}
