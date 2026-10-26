// Host-only helper. No DSH imports: callers inject the official services.
import { constants } from 'node:fs';
import { lstat, realpath, open, link, rename, unlink } from 'node:fs/promises';
import { isAbsolute, join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

const pending = new Map();
const mappingName = 'main-session.json';
const sessionIdPattern = /^session-[A-Za-z0-9-]{1,160}$/;

async function directory(path, privateDirectory = false) {
  if (typeof path !== 'string' || !isAbsolute(path) || resolve(path) !== path) {
    throw new Error('EVA main Session paths must be absolute and canonical.');
  }
  const stat = await lstat(path);
  if (!stat.isDirectory() || stat.isSymbolicLink() || await realpath(path) !== path) {
    throw new Error('EVA main Session directory must not be a symlink.');
  }
  if (privateDirectory && ((stat.mode & 0o077) !== 0 ||
      (process.getuid && stat.uid !== process.getuid()))) {
    throw new Error('EVA main Session state directory must be private and owned by this user.');
  }
}

async function syncDirectory(path) {
  const handle = await open(path, constants.O_RDONLY | constants.O_DIRECTORY);
  try { await handle.sync(); } finally { await handle.close(); }
}

async function readMapping(path, expected) {
  let stat;
  try { stat = await lstat(path); }
  catch (error) { if (error.code === 'ENOENT') return undefined; throw error; }
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 ||
      (stat.mode & 0o077) !== 0 || (process.getuid && stat.uid !== process.getuid())) {
    throw new Error('EVA refuses unsafe main Session mapping (including symlinks).');
  }
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  let value;
  try {
    const opened = await handle.stat();
    if (opened.ino !== stat.ino || opened.dev !== stat.dev || opened.size > 4096) {
      throw new Error('EVA main Session mapping changed or is oversized.');
    }
    value = JSON.parse(await handle.readFile('utf8'));
  } catch (cause) {
    throw new Error('EVA refuses corrupt main Session mapping.', { cause });
  } finally { await handle.close(); }
  const legacyWorkspace = join(expected.workspace, 'workspace');
  const keys = value && Object.keys(value).sort().join(',');
  const validVersion = value?.version === 1
    ? keys === 'profile,sessionId,state,version,workspace' &&
      [expected.workspace, legacyWorkspace].includes(value.workspace)
    : value?.version === 2 &&
      keys === 'originalWorkspace,profile,sessionId,state,version,workspace' &&
      value.workspace === expected.workspace &&
      [expected.workspace, legacyWorkspace].includes(value.originalWorkspace);
  if (!value || !validVersion || !['reserved', 'ready'].includes(value.state) ||
      !sessionIdPattern.test(value.sessionId) || value.profile !== expected.profile) {
    throw new Error('EVA refuses corrupt or mismatched main Session mapping.');
  }
  return value;
}

// Publish complete, fsynced JSON only. An initial reservation never replaces an
// existing mapping; the Host's existing owner lock serializes other processes.
async function publish(stateDir, value, initial) {
  const target = join(stateDir, mappingName);
  const temporary = join(stateDir, `.main-session-${randomUUID()}.tmp`);
  const handle = await open(temporary, 'wx', 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(value)}\n`);
    await handle.sync();
  } finally { await handle.close(); }
  try {
    if (initial) await link(temporary, target);
    else await rename(temporary, target);
    await syncDirectory(stateDir);
  } finally {
    // Only this function's exclusive, fixed-directory temporary file is removed.
    if (resolve(temporary) !== temporary || !temporary.startsWith(`${stateDir}/.main-session-`)) {
      throw new Error('Unsafe EVA temporary mapping path.');
    }
    await unlink(temporary).catch((error) => { if (error.code !== 'ENOENT') throw error; });
    await syncDirectory(stateDir);
  }
}

async function inspectMain(ctx, mapping) {
  let inspection;
  try { inspection = await ctx.sessionController.inspect(mapping.sessionId); }
  catch (cause) {
    // Missing, corrupt, or unavailable is never permission to create a replacement.
    throw new Error('EVA main Session is missing or unreadable; refusing replacement. Inspect the mapping and Session storage manually.', { cause });
  }
  const meta = inspection?.meta;
  if (!meta || meta.id !== mapping.sessionId || meta.cwd !== (mapping.originalWorkspace ?? mapping.workspace) ||
      meta.parentSession !== undefined || meta.origin !== undefined || meta.isSeeded !== false) {
    throw new Error('EVA main Session inspection does not match its ordinary workspace Session.');
  }
}

async function ensure(ctx, options) {
  const { workspace, profile, stateDir } = options;
  if (typeof profile !== 'string' || !/^[a-z][a-z0-9-]{0,31}$/.test(profile)) {
    throw new Error('Invalid EVA main Session profile.');
  }
  if (typeof ctx?.sessionController?.create !== 'function' ||
      typeof ctx.sessionController.inspect !== 'function' ||
      typeof ctx.sessionPersistence?.flush !== 'function') {
    throw new Error('EVA main Session requires injected sessionController and sessionPersistence services.');
  }
  await directory(stateDir, true);
  await directory(workspace);
  const path = join(stateDir, mappingName);
  let mapping = await readMapping(path, options);
  if (!mapping) {
    // Reserve identity BEFORE invoking create. A crash cannot leave an untracked
    // Session. An interrupted reservation with no Session fails closed on restart:
    // deletion and creation-not-yet-run cannot be safely distinguished.
    mapping = { version: 2, state: 'reserved', profile, workspace, originalWorkspace: workspace, sessionId: `session-${randomUUID()}` };
    await publish(stateDir, mapping, true);
    const created = await ctx.sessionController.create({ cwd: workspace, sessionId: mapping.sessionId });
    if (created?.sessionId !== mapping.sessionId) {
      throw new Error('EVA Session controller returned a different main Session identity; refusing to adopt it.');
    }
  }
  await inspectMain(ctx, mapping);
  const migrate = mapping.version === 1 && mapping.workspace !== workspace;
  if (migrate) {
    // The immutable header retains its original cwd. Change only the effective
    // directory through the official event-producing owner, never storage bytes.
    // Leave the v1 intent intact until flush succeeds so an interrupted upgrade
    // retries the same identity and idempotent set, never create or a new ID.
    await directory(mapping.workspace);
    if (typeof ctx.sessionController.resolveAgent !== 'function' ||
        typeof ctx.workingDirectory?.set !== 'function') {
      throw new Error('EVA main Session upgrade requires injected workingDirectory and sessionController.resolveAgent.');
    }
    const resolved = await ctx.sessionController.resolveAgent(mapping.sessionId);
    if (!resolved?.agent || resolved.error || resolved.agent.id !== mapping.sessionId) {
      throw new Error('EVA main Session upgrade could not resolve its exact Agent.', { cause: resolved?.error });
    }
    const canonical = await ctx.workingDirectory.set(resolved.agent, workspace);
    if (canonical !== workspace) throw new Error('EVA main Session upgrade resolved an unexpected directory.');
  }
  // inspect can return attached state before storage materializes. The official
  // service-wide barrier drains routed writes and forces even blank Sessions to
  // survive process restart. A read handle's flush is NOT such a barrier.
  await ctx.sessionPersistence.flush();
  if (mapping.state !== 'ready' || migrate) {
    const current = await readMapping(path, options);
    if (!current || JSON.stringify(current) !== JSON.stringify(mapping)) {
      throw new Error('EVA main Session mapping changed during initialization.');
    }
    const ready = migrate
      ? { ...mapping, version: 2, state: 'ready', workspace, originalWorkspace: mapping.workspace }
      : { ...mapping, state: 'ready' };
    await publish(stateDir, ready, false);
  }
  return { sessionId: mapping.sessionId };
}

/**
 * Ensure one persistent main ordinary Session without touching other Sessions.
 * Inject `sessionController` and `sessionPersistence`, then call after appReady
 * and workspace registration, under the EVA Host's existing single-process lock.
 * `workspace` and `stateDir` are existing canonical absolute paths; `stateDir`
 * is the private home/eva directory. No preset is forced: create uses the saved
 * default. Returns Promise<{sessionId: string}>; repeated calls only inspect.
 * A v1 mapping from exactly workspace/workspace upgrades the same Session using
 * injected `workingDirectory.set` and `sessionController.resolveAgent`, then
 * flushes before publishing v2. Completed upgrades never reset a user's cwd.
 * A corrupt mapping, missing main, or ambiguous pre-create crash fails closed.
 */
export function ensureMainSession(ctx, options) {
  const key = options?.stateDir;
  const active = pending.get(key);
  if (active) {
    if (active.ctx !== ctx || active.workspace !== options.workspace || active.profile !== options.profile) {
      return Promise.reject(new Error('Conflicting concurrent EVA main Session initialization.'));
    }
    return active.promise;
  }
  const entry = { ctx, workspace: options?.workspace, profile: options?.profile };
  entry.promise = ensure(ctx, options ?? {}).finally(() => {
    if (pending.get(key) === entry) pending.delete(key);
  });
  pending.set(key, entry);
  return entry.promise;
}

/** Pin only this node's fixed main title using the native user-rename owner. */
export async function ensureMainSessionTitle(ctx, sessionId, profile) {
  const title = profile.toUpperCase();
  const current = await ctx.sessionQuery.readTitle(sessionId);
  // Equal generated text is not enough: only a user title suppresses later
  // automatic generation. Already-pinned names need no duplicate log event.
  if (current?.title === title && current.source.kind === 'user') return;
  const accepted = await ctx.sessionController.rename({ sessionId, title });
  if (accepted?.title !== title || !Number.isSafeInteger(accepted.seq) || accepted.seq < 0) {
    throw new Error('EVA main Session title was not accepted.');
  }
  await ctx.sessionPersistence.flush();
}

/**
 * Native blank-session composition requires durable Workspace membership, not
 * just a selected Session with a cwd. Use the immutable header's owner: a legacy
 * main may have an effective working directory different from its original one.
 * Registration never creates a directory or changes the Session's cwd/history.
 */
export async function attachMainSessionWorkspace(ctx, sessionId) {
  const { meta } = await ctx.sessionController.inspect(sessionId);
  if (meta?.id !== sessionId || typeof meta.cwd !== 'string' || !isAbsolute(meta.cwd)) {
    throw new Error('EVA main Session has no valid Workspace owner.');
  }
  const workspace = await ctx.workspaceRegistry.create(meta.cwd);
  await workspace.attachSession(sessionId);
  if (!workspace.sessionIds.includes(sessionId)) {
    throw new Error('EVA main Session Workspace membership was not published.');
  }
}
