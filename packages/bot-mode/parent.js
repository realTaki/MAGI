// Parent Bot identity policy. Keep the existing profile-local namespace unchanged.
import { constants } from 'node:fs';
import { lstat, realpath, mkdir, open } from 'node:fs/promises';
import { isAbsolute, join, resolve } from 'node:path';
import { ensureMainSession, attachMainSessionWorkspace } from './parent-main-session.js';


async function directory(path, privateDirectory = false) {
  if (typeof path !== 'string' || !isAbsolute(path) || resolve(path) !== path) {
    throw new Error('Parent Bot paths must be canonical absolute directories.');
  }
  const stat = await lstat(path);
  if (!stat.isDirectory() || stat.isSymbolicLink() || await realpath(path) !== path ||
      (privateDirectory && ((stat.mode & 0o077) !== 0 || (process.getuid && stat.uid !== process.getuid())))) {
    throw new Error('Unsafe parent Bot directory.');
  }
}

// Fail closed on malformed/redirected markers; never initialize inside an EVA
// child, including a child whose active profile has subsequently changed.
export async function isEvaChildHome(home) {
  await directory(home);
  const state = join(home, 'eva');
  try { await lstat(state); }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
  await directory(state);
  const path = join(state, 'node.json');
  let stat;
  try { stat = await lstat(path); }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || stat.size > 4096) {
    throw new Error('Unsafe EVA role marker.');
  }
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const opened = await handle.stat();
    if (opened.ino !== stat.ino || opened.dev !== stat.dev || opened.size > 4096) {
      throw new Error('EVA role marker changed.');
    }
    if (JSON.parse(await handle.readFile('utf8'))?.role !== 'eva-child') {
      throw new Error('Invalid EVA role marker.');
    }
    return true;
  } finally { await handle.close(); }
}

async function createDirectory(path, privateDirectory = false) {
  try { await mkdir(path, { mode: 0o700 }); }
  catch (error) { if (error.code !== 'EEXIST') throw error; }
  await directory(path, privateDirectory);
}

/**
 * Official domain operation for startup and tests; no live operation is invoked
 * by Inspect, HTTP GET, or importing this module. Uses the saved default preset.
 * The fixed namespace is profile-local, not a restriction on profile names.
 * Missing reserved/ready Sessions fail closed; unrelated Sessions are untouched.
 */
export async function initializeParentMain(ctx) {
  const home = ctx.profileContext.home;
  if (await isEvaChildHome(home)) return undefined;
  await directory(ctx.profileContext.dir);
  const workspace = join(home, 'workspace');
  // Compatibility namespace: retain the exact pre-merge identity, not a new Session.
  const stateDir = join(ctx.profileContext.dir, 'eva-parent-main');
  await createDirectory(workspace);
  await createDirectory(stateDir, true);
  await ctx.workspaceRegistry.create(workspace, 'DSH');
  const main = await ensureMainSession(ctx, { workspace, stateDir, profile: 'parent-main' });
  await attachMainSessionWorkspace(ctx, main.sessionId);
  return {
    handle: 'dsh-main', kind: 'parent', status: 'running', workspace,
    mainSessionId: main.sessionId,
  };
}
