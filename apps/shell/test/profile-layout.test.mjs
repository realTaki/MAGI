import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import dugite from 'dugite';
import { createProcessOwner } from '../process-owner.mjs';
import { profilePaths, prepareShellProfile } from '../profile-layout.mjs';

const resolved = process.env.MAGI_GIT || dugite.resolveGitBinary();
const git = existsSync(resolved) ? resolved : 'git';
const env = existsSync(resolved)
  ? { ...dugite.setupEnvironment({}).env, LOCAL_GIT_DIRECTORY: path.dirname(path.dirname(git)) }
  : { ...process.env };
async function fixture(t) {
  const home = await fs.mkdtemp(path.join(tmpdir(), 'magi-profile-'));
  const owner = createProcessOwner();
  t.after(async () => { await owner.close(); await fs.rm(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); });
  const run = (args, cwd) => owner.command(git, args, { cwd, env });
  const prepare = (options = {}) => prepareShellProfile({ home, repository: path.join(home, 'origin'), tools: { git, env }, command: owner.command, assertOpen: owner.assertOpen, ...options });
  const paths = profilePaths(home);
  return { home, owner, run, prepare, ...paths };
}
async function repository(f, root) {
  await fs.mkdir(root, { recursive: true });
  await f.run(['init', '--initial-branch=main'], root);
  await f.run(['config', 'user.name', 'Profile Fixture'], root);
  await f.run(['config', 'user.email', 'profile@example.invalid'], root);
  await fs.writeFile(path.join(root, 'source.ts'), 'export const version = 1;\n');
  await fs.writeFile(path.join(root, '.gitignore'), '/cache/\nbuild/\ndist/\nnode_modules/\n');
  await f.run(['add', '.'], root); await f.run(['commit', '-m', 'fixture'], root);
}
async function has(file) { try { await fs.lstat(file); return true; } catch (e) { if (e.code === 'ENOENT') return false; throw e; } }

test('profile naming handles native Windows paths without renaming per-app workspaces', () => {
  assert.deepEqual(profilePaths('C:\\Users\\Someone', path.win32), { dataRoot: 'C:\\Users\\Someone\\.magi', shell: 'C:\\Users\\Someone\\.magi\\shell', cache: 'C:\\Users\\Someone\\.magi\\shell\\cache' });
});

test('fresh bootstrap tolerates Electron precreating cache; repeat startup leaves source and records unchanged', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  await fs.mkdir(path.join(f.cache, 'electron'), { recursive: true });
  await fs.writeFile(path.join(f.cache, 'electron', 'receipt'), 'existing browser cache');
  assert.equal(await f.prepare(), f.shell);
  const head = await f.run(['rev-parse', 'HEAD'], f.shell);
  assert.equal((await f.run(['status', '--porcelain'], f.shell)).trim(), '');
  await fs.writeFile(path.join(f.shell, 'source.ts'), 'dirty shell source');
  assert.equal(await f.prepare(), f.shell);
  assert.equal(await f.run(['rev-parse', 'HEAD'], f.shell), head);
  assert.equal(await fs.readFile(path.join(f.cache, 'electron', 'receipt'), 'utf8'), 'existing browser cache');
  assert.equal(await fs.readFile(path.join(f.shell, 'source.ts'), 'utf8'), 'dirty shell source');
});

test('partial fresh clone resumes per-entry installation', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  let interrupted = false;
  await assert.rejects(f.prepare({ rename: async (source, target) => { if (path.basename(source) === 'source.ts' && !interrupted) { interrupted = true; throw new Error('simulated bootstrap interruption'); } return fs.rename(source, target); } }), /bootstrap interruption/);
  assert.equal(await has(path.join(f.dataRoot, '.shell-layout.json')), true);
  await f.prepare(); assert.equal((await f.run(['status', '--porcelain'], f.shell)).trim(), '');
});

test('a live preparation lock stops without replacing files', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  const lock = path.join(f.dataRoot, '.shell-layout.lock');
  await fs.mkdir(f.dataRoot, { recursive: true });
  await fs.writeFile(lock, JSON.stringify({ pid: process.pid })); await assert.rejects(f.prepare(), /already running/); await fs.unlink(lock);
});

test('a dead preparation owner can be recovered without signalling unrelated processes', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  await fs.mkdir(f.dataRoot);
  const child = f.owner.spawn(process.execPath, ['-e', 'process.exit(0)'], { stdio: 'ignore' });
  await new Promise((resolve) => child.once('close', resolve)); await f.owner.reap(child);
  await fs.writeFile(path.join(f.dataRoot, '.shell-layout.lock'), JSON.stringify({ pid: child.pid }));
  assert.equal(await f.prepare(), f.shell);
  assert.equal(await has(path.join(f.dataRoot, '.shell-layout.lock')), false);
});
