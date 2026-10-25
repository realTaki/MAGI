import { constants } from 'node:fs';
import { cp, lstat, mkdir, open, readdir, realpath, rename, rm, unlink, writeFile } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';

function refuse(message) { throw new Error(`Workspace relocation refused: ${message}`); }
function absolute(path) {
  if (typeof path !== 'string' || !isAbsolute(path) || resolve(path) !== path) refuse('paths must be absolute and canonical.');
}
function within(root, path) {
  const delta = relative(root, path);
  return delta === '' || (delta !== '..' && !delta.startsWith(`..${sep}`) && !isAbsolute(delta));
}
async function statOrNull(path) {
  try { return await lstat(path); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}
async function directory(path) {
  const stat = await lstat(path);
  if (!stat.isDirectory() || stat.isSymbolicLink() || await realpath(path) !== path) refuse('directory is not canonical or is a symlink.');
  return stat;
}
async function missing(path) {
  if (await statOrNull(path)) refuse('destination already exists.');
}
function identity(stat) { return `${stat.dev}:${stat.ino}`; }
async function regularBytes(path) {
  const before = await lstat(path);
  if (!before.isFile() || before.isSymbolicLink()) refuse('metadata must be a regular file, not a symlink.');
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const opened = await file.stat();
    if (identity(before) !== identity(opened)) refuse('metadata ownership changed.');
    return { bytes: await file.readFile(), id: identity(opened) };
  } finally { await file.close(); }
}
function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function registryRecords(registry) {
  if (!object(registry) || !object(registry.unit) || registry.unit.name !== 'workspace' || registry.unit.version !== 2 ||
      !object(registry.global) || !object(registry.tables) || !object(registry.tables.workspaces) ||
      Object.keys(registry.tables).some((key) => key !== 'workspaces') ||
      !Array.isArray(registry.global.workspaceIds)) refuse('unsupported workspace registry structure.');
  const ids = registry.global.workspaceIds;
  const records = registry.tables.workspaces;
  if (new Set(ids).size !== ids.length || Object.keys(records).length !== ids.length) refuse('workspace registry IDs do not match records.');
  return ids.map((id) => {
    if (typeof id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ||
        !Object.hasOwn(records, id) || !object(records[id])) refuse('invalid registered workspace UUID.');
    absolute(records[id].path);
    return { id, record: records[id] };
  });
}
function rejectHistory(value) {
  if (!value || typeof value !== 'object') return;
  for (const [key, item] of Object.entries(value)) {
    if (['sessionIds', 'archivedSessionIds', 'pinnedSessionIds'].includes(key) && (!Array.isArray(item) || item.length)) refuse('historical sessions are unsupported.');
    if (key === 'pendingMutation' && item !== null && item !== false) refuse('pendingMutation is unsupported.');
    rejectHistory(item);
  }
}

// The fingerprints never leave this module. Both content and inode/metadata changes
// invalidate deletion authority; links, devices, sockets and FIFOs are never copied.
async function fingerprint(root) {
  const entries = [];
  async function visit(path, name) {
    const before = await lstat(path, { bigint: true });
    if (before.isSymbolicLink()) refuse('source or destination contains a symlink.');
    const stamp = [before.dev, before.ino, before.mode, before.size, before.mtimeNs, before.ctimeNs].map(String);
    if (before.isDirectory()) {
      entries.push({ name, kind: 'directory', stamp });
      for (const child of (await readdir(path)).sort()) await visit(join(path, child), name ? `${name}/${child}` : child);
    } else if (before.isFile()) {
      const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
      let hash;
      try {
        if (identity(await file.stat({ bigint: true })) !== identity(before)) refuse('source ownership changed.');
        const digest = createHash('sha256');
        for await (const chunk of file.createReadStream({ autoClose: false })) digest.update(chunk);
        hash = digest.digest('hex');
      } finally { await file.close(); }
      entries.push({ name, kind: 'file', stamp, hash });
    } else refuse('tree must contain only regular files and directories.');
    const after = await lstat(path, { bigint: true });
    if (stamp.join(':') !== [after.dev, after.ino, after.mode, after.size, after.mtimeNs, after.ctimeNs].map(String).join(':')) refuse('tree changed during inspection.');
  }
  await directory(root);
  await visit(root, '');
  return entries;
}
function sameTree(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function contents(tree) { return tree.map(({ name, kind, hash }) => ({ name, kind, ...(hash ? { hash } : {}) })); }

/** Called only while runtime.startNode owns home/eva/owner.lock. Returns a path.
 * No defaults: in particular this helper never discovers or probes a real user home.
 * Failed transactions are quarantined permanently by the exclusive pending marker.
 */
export async function ensureWorkspace({ home, handle, legacyWorkspace }) {
  absolute(home);
  if (typeof handle !== 'string' || !/^[a-z][a-z0-9-]{0,31}$/.test(handle)) refuse('invalid handle.');
  await directory(home);
  const workspace = join(home, 'workspace');
  if (dirname(workspace) !== home || basename(workspace) !== 'workspace') refuse('unsafe nested target.');
  const state = join(home, 'eva');
  await directory(state);
  const pending = join(state, 'workspace-relocation.pending.json');
  if (await statOrNull(pending)) refuse('incomplete workspace relocation; inspect the pending marker before starting.');
  const storages = join(home, 'storages');
  const registryPath = join(storages, 'workspace.json');
  const storageStat = await statOrNull(storages);
  if (storageStat) await directory(storages);
  const registryStat = storageStat ? await statOrNull(registryPath) : null;
  if (!registryStat) {
    // Fresh nodes never inspect legacyWorkspace (not even its ancestors).
    try { await mkdir(workspace, { mode: 0o700 }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
    await directory(workspace);
    return workspace;
  }
  const originalRegistry = await regularBytes(registryPath);
  let registry;
  try { registry = JSON.parse(originalRegistry.bytes.toString('utf8')); }
  catch { refuse('invalid workspace registry JSON.'); }
  const records = registryRecords(registry);
  const nested = records.filter(({ record }) => record.path === workspace);
  if (nested.length > 1) refuse('duplicate nested workspace records.');
  if (nested.length === 1 || records.length === 0) {
    try { await mkdir(workspace, { mode: 0o700 }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
    await directory(workspace);
    return workspace;
  }

  if (records.length !== 1) refuse('expected exactly one registered legacy workspace.');
  const { id, record } = records[0];
  absolute(legacyWorkspace);
  if (record.path !== legacyWorkspace || basename(legacyWorkspace) !== `${handle}-workspace` ||
      within(home, legacyWorkspace) || within(legacyWorkspace, home)) refuse('unexpected or overlapping legacy workspace path.');
  rejectHistory(registry);
  await missing(workspace); // Never merge, including an empty pre-existing target.
  await directory(legacyWorkspace); // A missing source refuses before any mutation.
  const sourceParent = dirname(legacyWorkspace);
  const parentId = identity(await directory(sourceParent));
  const sourceTree = await fingerprint(legacyWorkspace);
  const sourceId = sourceTree[0].stamp.slice(0, 2).join(':');
  const homeId = identity(await directory(home));
  const stateId = identity(await directory(state));
  const storageId = identity(await directory(storages));
  const factsPath = join(state, 'workspace-relocation.json');
  await missing(factsPath);
  const facts = { handle, workspaceId: id, source: legacyWorkspace, workspace, backupRetained: false };
  const pendingText = JSON.stringify(facts, null, 2) + '\n';
  await writeFile(pending, pendingText, { flag: 'wx', mode: 0o600 });

  async function guards() {
    if (identity(await directory(home)) !== homeId || identity(await directory(state)) !== stateId ||
        identity(await directory(storages)) !== storageId || identity(await directory(sourceParent)) !== parentId ||
        identity(await directory(workspace)) !== targetId ||
        workspace !== join(home, 'workspace') || pending !== join(home, 'eva', 'workspace-relocation.pending.json') ||
        registryPath !== join(home, 'storages', 'workspace.json') ||
        (await regularBytes(pending)).bytes.toString('utf8') !== pendingText) refuse('transaction path ownership changed.');
  }
  // From here on, every failure intentionally leaves the marker and all data.
  await mkdir(workspace, { mode: 0o700 }); // Exclusive reservation; no recursive mkdir.
  const targetId = identity(await directory(workspace));
  for (const entry of await readdir(legacyWorkspace)) {
    await guards();
    const from = join(legacyWorkspace, entry);
    const to = join(workspace, entry);
    await missing(to);
    await cp(from, to, { recursive: true, force: false, errorOnExist: true, preserveTimestamps: true, dereference: false });
  }
  if (!sameTree(sourceTree, await fingerprint(legacyWorkspace)) ||
      !sameTree(contents(sourceTree), contents(await fingerprint(workspace)))) refuse('copied tree or source changed.');
  await guards();
  const current = await regularBytes(registryPath);
  if (current.id !== originalRegistry.id || !current.bytes.equals(originalRegistry.bytes)) refuse('registry changed during relocation.');
  record.path = workspace; // Preserve UUID, title and every other registry field.
  const temporary = join(storages, `.workspace-relocation-${randomUUID()}.tmp`);
  const committedRegistry = JSON.stringify(registry, null, 2) + '\n';
  await writeFile(temporary, committedRegistry, { flag: 'wx', mode: 0o600 });
  await guards();
  const beforeRename = await regularBytes(registryPath);
  const stagedRegistry = await regularBytes(temporary);
  if (beforeRename.id !== originalRegistry.id || !beforeRename.bytes.equals(originalRegistry.bytes) ||
      stagedRegistry.bytes.toString('utf8') !== committedRegistry || dirname(temporary) !== storages) refuse('registry ownership changed before commit.');
  await rename(temporary, registryPath);
  await writeFile(factsPath, pendingText, { flag: 'wx', mode: 0o600 });
  await guards();
  if ((await regularBytes(registryPath)).bytes.toString('utf8') !== committedRegistry ||
      identity(await directory(workspace)) !== targetId ||
      !sameTree(contents(sourceTree), contents(await fingerprint(workspace))) ||
      !sameTree(sourceTree, await fingerprint(legacyWorkspace))) refuse('source or destination changed before cleanup.');
  // Exact caller-authorized source only, never Documents or any other parent.
  if (legacyWorkspace !== record.path && basename(legacyWorkspace) === `${handle}-workspace` &&
      !within(legacyWorkspace, home) && !within(home, legacyWorkspace) &&
      identity(await directory(sourceParent)) === parentId &&
      identity(await directory(legacyWorkspace)) === sourceId) {
    await rm(legacyWorkspace, { recursive: true, force: false });
  } else refuse('unsafe source cleanup path.');
  if (await statOrNull(legacyWorkspace)) refuse('original workspace was not removed.');
  await guards();
  await unlink(pending);
  return workspace;
}
