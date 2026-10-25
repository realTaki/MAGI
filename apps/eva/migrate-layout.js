import { cp, lstat, mkdir, open, readFile, readdir, realpath, rename, unlink, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { EvaRuntime } from './runtime.js';

function within(root, path) {
  const delta = relative(root, path);
  return delta === '' || (!delta.startsWith(`..${sep}`) && delta !== '..' && !isAbsolute(delta));
}

async function directory(path) {
  const stat = await lstat(path);
  if (!stat.isDirectory() || stat.isSymbolicLink() || await realpath(path) !== path) {
    throw new Error('Migration requires canonical directories without symlinks.');
  }
}

// Reject all links (including links staying inside the source), devices, and sockets.
async function inspectTree(path) {
  const stat = await lstat(path);
  if (stat.isSymbolicLink()) throw new Error('Migration refuses source symlinks.');
  if (stat.isDirectory()) {
    for (const entry of await readdir(path)) await inspectTree(join(path, entry));
  } else if (!stat.isFile()) throw new Error('Migration source must contain only regular files and directories.');
}

async function missing(path) {
  try { await lstat(path); }
  catch (error) { if (error.code === 'ENOENT') return; throw error; }
  throw new Error('Migration refuses an occupied destination.');
}

async function jsonFile(path) {
  const stat = await lstat(path);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('Migration metadata must be a regular file.');
  return JSON.parse(await readFile(path, 'utf8'));
}

function rejectHistory(value) {
  if (!value || typeof value !== 'object') return;
  for (const [key, item] of Object.entries(value)) {
    if (/^(?:sessionIds|archivedSessionIds|pinnedSessionIds)$/.test(key)) {
      if (!Array.isArray(item) || item.length) throw new Error('Historical sessions are not supported by offline migration.');
    }
    if (key === 'pendingMutation' && item !== null && item !== false) {
      throw new Error('Migration refuses pendingMutation.');
    }
    rejectHistory(item);
  }
}

function updateRegistry(registry, sourceWorkspace, workspace, handle) {
  rejectHistory(registry);
  const records = registry?.tables?.workspaces;
  const ids = registry?.global?.workspaceIds;
  if (!records || typeof records !== 'object' || Array.isArray(records) || !Array.isArray(ids)) {
    throw new Error('Unsupported workspace registry structure.');
  }
  const matches = Object.entries(records).filter(([, record]) => record?.path === sourceWorkspace);
  if (matches.length !== 1 || !ids.includes(matches[0][0])) {
    throw new Error('Expected exactly one registered source workspace.');
  }
  const [workspaceId, record] = matches[0];
  record.path = workspace;
  record.title = `EVA · ${handle}`;
  return workspaceId;
}

/** Offline copy only: no Host spawn, signals, credential inspection, or source removal. */
export async function migrateLegacy({
  legacyRoot = join(process.cwd(), '.eva', 'nodes', 'second'),
  homeRoot = homedir(),
  workspaceRoot,
} = {}) {
  legacyRoot = resolve(legacyRoot);
  homeRoot = resolve(homeRoot);
  if (workspaceRoot !== undefined) throw new Error('Custom workspace roots are no longer supported; workspace stays inside the node home.');
  const sourceHome = join(legacyRoot, 'home');
  const sourceWorkspace = join(legacyRoot, 'workspace');
  const oldLock = join(legacyRoot, 'owner.lock');
  const protectedHome = join(homeRoot, '.dsh');
  if (legacyRoot === dirname(legacyRoot) || within(legacyRoot, homeRoot) ||
      within(protectedHome, legacyRoot) || basename(homeRoot) === '.dsh') {
    throw new Error('Unsafe migration source/destination structure.');
  }
  await directory(homeRoot);
  await directory(legacyRoot);
  await directory(sourceHome);
  await directory(sourceWorkspace);
  const allocator = new EvaRuntime(null, { homeRoot });
  const lockText = JSON.stringify({ ownerPid: process.pid, token: randomUUID(), purpose: 'offline-migration' });
  let lock;
  try { lock = await open(oldLock, 'wx', 0o600); }
  catch (error) {
    if (error.code === 'EEXIST') throw new Error('Legacy node is locked; stop its controller and inspect the lock before migration.');
    throw error;
  }
  let committed = false;
  let ownedText = lockText;
  try {
    await lock.writeFile(lockText);
    await inspectTree(legacyRoot);
    const handle = await allocator.allocateHandle();
    const allocated = allocator.paths(handle);
    // Offline imports retain the legacy payload layout; new main Sessions use
    // the node home independently. Never merge imported files into Host state.
    const target = { ...allocated, workspace: join(allocated.home, 'workspace') };
    if (!/^eva-\d{3,}$/.test(handle) || target.home !== join(homeRoot, `.dsh-${handle}`) ||
        target.workspace !== join(target.home, 'workspace') ||
        target.state !== join(target.home, 'eva') || target.profileDir !== join(target.home, 'profiles', handle) ||
        within(legacyRoot, target.home) || within(legacyRoot, target.workspace) ||
        within(target.home, legacyRoot) || within(target.workspace, legacyRoot)) {
      throw new Error('Unsafe allocated migration destination.');
    }
    await directory(target.home);
    await missing(target.workspace);
    if ((await readdir(target.home)).length) {
      throw new Error('Migration requires empty allocated directories.');
    }
    await mkdir(target.state, { mode: 0o700 });
    const pendingPath = join(target.state, 'migration.pending.json');
    const facts = { handle, profile: handle, source: { root: legacyRoot, home: sourceHome, workspace: sourceWorkspace },
      destination: { home: target.home, workspace: target.workspace, profileDir: target.profileDir }, backupRetained: true };
    const pendingText = JSON.stringify(facts, null, 2) + '\n';
    await writeFile(pendingPath, pendingText, { flag: 'wx', mode: 0o600 });
    await mkdir(target.workspace, { mode: 0o700 });

    // Copy entries into exclusively claimed roots. Never merge or overwrite existing entries.
    for (const [source, destination] of [[sourceHome, target.home], [sourceWorkspace, target.workspace]]) {
      for (const entry of await readdir(source)) {
        const from = join(source, entry);
        const to = join(destination, entry);
        await inspectTree(from);
        await missing(to);
        await cp(from, to, { recursive: true, force: false, errorOnExist: true, preserveTimestamps: true });
      }
    }
    await directory(target.home);
    await directory(target.workspace);
    await inspectTree(target.home);
    await inspectTree(target.workspace);
    const profiles = join(target.home, 'profiles');
    const oldProfile = join(profiles, 'eva-second');
    await directory(profiles);
    await directory(oldProfile);
    await missing(target.profileDir);
    // Both operands are verified exact children of the exclusively reserved destination.
    if (dirname(oldProfile) !== profiles || basename(oldProfile) !== 'eva-second' ||
        target.profileDir !== join(profiles, handle)) throw new Error('Unsafe profile rename.');
    await rename(oldProfile, target.profileDir);
    const manifestPath = join(target.profileDir, 'package.json');
    const manifest = await jsonFile(manifestPath);
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) throw new Error('Invalid copied package manifest.');
    manifest.name = `dsh-profile-${handle}`;
    await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
    const registryPath = join(target.home, 'storages', 'workspace.json');
    const registry = await jsonFile(registryPath);
    facts.workspaceId = updateRegistry(registry, sourceWorkspace, target.workspace, handle);
    await writeFile(registryPath, JSON.stringify(registry, null, 2) + '\n');
    await writeFile(join(target.state, 'migration.json'), JSON.stringify(facts, null, 2) + '\n', { flag: 'wx', mode: 0o600 });

    // A persistent lock tombstone prevents the old plugin from restarting the backup.
    await directory(legacyRoot);
    const currentLock = await lstat(oldLock);
    if (!currentLock.isFile() || currentLock.isSymbolicLink() || await readFile(oldLock, 'utf8') !== lockText) {
      throw new Error('Legacy migration lock ownership changed.');
    }
    ownedText = JSON.stringify({ ownerPid: process.pid, purpose: 'migration-tombstone', migration: facts });
    await lock.truncate(0);
    await lock.write(ownedText, 0, 'utf8');
    await lock.sync();
    committed = true;
    await directory(target.state);
    if (resolve(pendingPath) !== join(target.home, 'eva', 'migration.pending.json') ||
        await realpath(pendingPath) !== pendingPath || await readFile(pendingPath, 'utf8') !== pendingText) {
      throw new Error('Pending migration marker ownership changed.');
    }
    await unlink(pendingPath);
    return { status: 'migrated', ...facts };
  } finally {
    await lock.close();
    if (!committed) {
      await directory(legacyRoot);
      const stat = await lstat(oldLock).catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
      if (stat?.isFile() && !stat.isSymbolicLink() && oldLock === join(legacyRoot, 'owner.lock') &&
          await readFile(oldLock, 'utf8') === ownedText) await unlink(oldLock);
    }
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args[0] === '--') args.shift();
  if (args.length > 2 || args.some((arg) => arg.startsWith('--'))) {
    console.log(JSON.stringify({ status: 'refused', error: 'Expected optional legacyRoot and homeRoot positional arguments.' }));
    process.exitCode = 1;
  } else {
    const [legacyRoot, homeRoot] = args;
    try { console.log(JSON.stringify(await migrateLegacy({ legacyRoot, homeRoot }))); }
    catch {
      // Exception messages can contain malformed user metadata. CLI emits only safe facts.
      console.log(JSON.stringify({ status: 'refused', error: 'Offline migration failed; source retained. Inspect locks and pending markers before retrying.' }));
      process.exitCode = 1;
    }
  }
}
