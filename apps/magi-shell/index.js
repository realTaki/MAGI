// MAGI Shell Host: owns only the parent Main Bot, never the EVA node manager.
import { constants } from 'node:fs';
import { lstat, realpath, mkdir, open } from 'node:fs/promises';
import { isAbsolute, join, resolve } from 'node:path';
import { ensureMainSession, attachMainSessionWorkspace } from './main-session.js';

export const name = 'magi-shell';
export const inject = ['appReady', 'profileContext', 'connection', 'workspaceRegistry', 'sessionController', 'sessionPersistence'];
export const PARENT_MAIN_PATH = '/api/eva/parent-main';
const headers = { 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' };

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
async function childHome(home) {
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
  if (await childHome(home)) return undefined;
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

export async function apply(ctx) {
  let bot;
  let disposed = false;
  let validParent = false;
  // Probe before route registration so an EVA child contributes nothing. Invalid
  // parent paths/role markers get only a sanitized unavailable route, no writes.
  try {
    if (await childHome(ctx.profileContext.home)) return;
    validParent = true;
  } catch { /* Publish only the fixed unavailable response below. */ }
  ctx.effect(() => {
    const disposeRoute = ctx.connection.fetch.register({ path: PARENT_MAIN_PATH, methods: ['GET'], requestBody: 'buffered',
      async fetch() {
        // Native Connection authenticates/adopts the browser or API Peer;
        // this exact Fetch handler does not implement a second login path.
        return bot && !disposed
          ? Response.json({ mode: 'parent', bot }, { headers })
          : Response.json({ code: 'parent-main-unavailable', error: 'The parent Bot is not ready.' }, { status: 503, headers });
      },
    });
    const cancelReady = validParent ? ctx.appReady.onReady(() =>
      initializeParentMain(ctx).then((value) => {
        if (!disposed) bot = value;
      }).catch(() => { /* Readiness stays unavailable; never leak raw paths/errors. */ })) : undefined;
    return () => { disposed = true; bot = undefined; cancelReady?.(); return disposeRoute(); };
  });
}
