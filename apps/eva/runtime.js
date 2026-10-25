import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdir, realpath, lstat, readFile, writeFile, open, unlink, access, readdir } from 'node:fs/promises';
import { basename, dirname, join, resolve, relative, isAbsolute } from 'node:path';
import { ensureWorkspace } from './relocate-workspace.js';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { randomUUID, createHash } from 'node:crypto';

const here = dirname(fileURLToPath(import.meta.url));
const installationId = createHash('sha256').update(here).digest('hex').slice(0, 16);
const START_TIMEOUT = 60_000;
const STOP_TIMEOUT = 8_000;

export function validateName(name) {
  if (typeof name !== 'string' || !/^[a-z][a-z0-9-]{0,31}$/.test(name)) throw new Error('Node name must match [a-z][a-z0-9-]{0,31}.');
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

export class EvaRuntime {
  constructor(runtime, { startupTimeout = START_TIMEOUT, stopTimeout = STOP_TIMEOUT, homeRoot = homedir() } = {}) {
    this.runtime = runtime;
    this.startupTimeout = startupTimeout;
    this.stopTimeout = stopTimeout;
    this.homeRoot = resolve(homeRoot);
    this.nodes = new Map();
    this.pending = new Map();
    this.disposed = false;
  }

  paths(handle) {
    validateName(handle);
    const home = join(this.homeRoot, `.dsh-${handle}`);
    const state = join(home, 'eva');
    return { handle, home, state, lock: join(state, 'owner.lock'), profile: handle,
      profileDir: join(home, 'profiles', handle), workspace: join(home, 'workspace') };
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
      origin: node.ready?.origin ?? null, workspaceId: node.ready?.workspaceId ?? null,
      exitCode: node.exitCode ?? null, exitSignal: node.exitSignal ?? null,
      credentials: 'Parent credentials are not copied; existing child data is preserved.',
    };
  }

  async status(base, name) {
    if (name === undefined) return { nodes: [...this.nodes.values()].map((node) => this.snapshot(node)) };
    const node = this.nodes.get(await this.key(base, name));
    return node ? this.snapshot(node) : { handle: validateName(name), name, status: 'not-started' };
  }

  async start(base, name, signal) {
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
    const operation = this.startNode(base, name, key, signal);
    this.pending.set(key, operation);
    try { return await operation; } finally { this.pending.delete(key); }
  }

  async startNode(base, name, key, signal) {
    const hostHome = await realpath(this.homeRoot);
    if (hostHome !== this.homeRoot) throw new Error('EVA home root must be canonical.');
    const home = await directories(hostHome, [`.dsh-${validateName(name)}`]);
    const state = await directories(home, ['eva']);
    const release = await lockNode(join(state, 'owner.lock'));
    let node;
    try {
      const migrationPending = await lstat(join(state, 'migration.pending.json')).then(() => true, (error) => { if (error.code === 'ENOENT') return false; throw error; });
      if (migrationPending) throw new Error('EVA has an incomplete migration; inspect migration.pending.json before starting.');
      await ensureWorkspace({ home, handle: name,
        legacyWorkspace: join(hostHome, 'Documents', 'deepseek-harness', `${name}-workspace`) });
      const workspace = join(home, 'workspace');
      const profile = name;
      const profileDir = await directories(home, ['profiles', profile]);
      // Include session defaults so earlier exclusive startup patches remain unchanged.
      const defaultAgentPreset = 'cordis'; // Creator mode, not a process-wide permission mode.
      const startupConfigId = createHash('sha256').update(JSON.stringify({ workspace, profile, defaultAgentPreset })).digest('hex').slice(0, 16);
      const patch = join(state, `startup-${installationId}-${startupConfigId}.patch.yml`);
      await exclusiveContent(patch, JSON.stringify([{ insert: [{
        id: 'eva-child', name: join(here, 'child.js'),
        config: { workspace, profile },
      }] }, {
        id: 'agent-preset-registry', name: '@deepseek-ai/dsh-agent-preset-registry',
        config: { default: defaultAgentPreset },
      }], null, 2) + '\n');
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
      const finish = async (code, exitSignal) => {
        if (node.finished) return;
        node.finished = true;
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
            let url;
            try { url = new URL(message.origin); } catch { settle(new Error('Invalid child origin.')); return; }
            if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1') { settle(new Error('Child is not loopback-only.')); return; }
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
