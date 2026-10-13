import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import dugite from 'dugite';
import { createProcessOwner } from '../process-owner.mjs';
import { profilePaths, prepareShellProfile } from '../profile-layout.mjs';

const git = process.env.MAGI_GIT || dugite.resolveGitBinary();
const env = { ...dugite.setupEnvironment({}).env, LOCAL_GIT_DIRECTORY: path.dirname(path.dirname(git)) };
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
  assert.deepEqual(profilePaths('C:\\Users\\Someone', path.win32), { dataRoot: 'C:\\Users\\Someone\\.magi', shell: 'C:\\Users\\Someone\\.magi\\shell', cache: 'C:\\Users\\Someone\\.magi\\shell\\cache', legacyShell: 'C:\\Users\\Someone\\.magi\\MAGI', legacyCache: 'C:\\Users\\Someone\\.magi\\cache' });
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

test('legacy main relocation repairs five linked app trees and preserves dirty sources, indexes, history, data and missing registrations', async (t) => {
  const f = await fixture(t); await repository(f, f.legacyShell);
  const legacyIgnore = 'build/\ndist/\nnode_modules/\n';
  await fs.writeFile(path.join(f.legacyShell, '.gitignore'), legacyIgnore);
  const trees = [];
  for (const name of ['user', 'asp', 'eva-000', 'eva-001', 'eva-002']) {
    const root = path.join(f.dataRoot, name, 'MAGI'); await fs.mkdir(path.dirname(root), { recursive: true });
    await f.run(['worktree', 'add', '-b', `magi/${name}`, root], f.legacyShell);
    await fs.writeFile(path.join(path.dirname(root), 'state.json'), `${name} durable state`);
    await fs.writeFile(path.join(root, 'source.ts'), `${name} dirty source`);
    const index = (await f.run(['rev-parse', '--path-format=absolute', '--git-path', 'index'], root)).trim();
    trees.push({ root, name, index: await fs.readFile(index), head: await f.run(['rev-parse', 'HEAD'], root), status: await f.run(['status', '--porcelain'], root) });
  }
  const missing = path.join(f.home, 'unrelated-missing');
  await f.run(['worktree', 'add', '--detach', missing], f.legacyShell);
  const registration = (await f.run(['rev-parse', '--path-format=absolute', '--git-dir'], missing)).trim();
  const missingRecord = await fs.readFile(path.join(registration, 'gitdir')); await fs.rm(missing, { recursive: true });
  await fs.writeFile(path.join(f.legacyShell, 'source.ts'), 'dirty shell source');
  const status = await f.run(['status', '--porcelain'], f.legacyShell);
  const mainIndex = await fs.readFile(path.join(f.legacyShell, '.git/index'));
  const history = await fs.readFile(path.join(f.legacyShell, '.git/logs/HEAD'));
  await fs.mkdir(path.join(f.legacyCache, 'npm'), { recursive: true }); await fs.writeFile(path.join(f.legacyCache, 'npm', 'receipt'), 'old npm');
  await fs.mkdir(path.join(f.cache, 'electron'), { recursive: true }); await fs.writeFile(path.join(f.cache, 'electron', 'receipt'), 'early browser');
  assert.equal(await f.prepare(), f.shell);
  assert.equal(await has(f.legacyShell), false); assert.equal(await has(f.legacyCache), false);
  assert.equal(await f.run(['status', '--porcelain'], f.shell), status);
  assert.equal(await fs.readFile(path.join(f.shell, '.gitignore'), 'utf8'), legacyIgnore, 'older source ignore bytes are preserved; generated cache is ignored locally');
  assert.deepEqual(await fs.readFile(path.join(f.shell, '.git/index')), mainIndex);
  assert.deepEqual(await fs.readFile(path.join(f.shell, '.git/logs/HEAD')), history);
  for (const item of trees) {
    assert.equal(await f.run(['rev-parse', 'HEAD'], item.root), item.head);
    assert.equal(await f.run(['status', '--porcelain'], item.root), item.status);
    const index = (await f.run(['rev-parse', '--path-format=absolute', '--git-path', 'index'], item.root)).trim();
    assert.deepEqual(await fs.readFile(index), item.index);
    assert.equal(await fs.readFile(path.join(path.dirname(item.root), 'state.json'), 'utf8'), `${item.name} durable state`);
  }
  assert.deepEqual(await fs.readFile(path.join(f.shell, '.git/worktrees', path.basename(registration), 'gitdir')), missingRecord);
  assert.equal(await fs.readFile(path.join(f.cache, 'npm', 'receipt'), 'utf8'), 'old npm');
  assert.equal(await fs.readFile(path.join(f.cache, 'electron', 'receipt'), 'utf8'), 'early browser');
  assert.equal(await f.prepare(), f.shell);
  assert.equal((await f.run(['worktree', 'list', '--porcelain'], f.shell)).match(/worktree /g).length, 7);
});

test('a linked legacy Shell uses Git move, rejects its lock, then keeps dirty source and registry', async (t) => {
  const f = await fixture(t), origin = path.join(f.home, 'origin'); await repository(f, origin);
  await fs.mkdir(f.dataRoot); await f.run(['worktree', 'add', '-b', 'shell-source', f.legacyShell], origin);
  await fs.writeFile(path.join(f.legacyShell, 'source.ts'), 'dirty linked shell');
  await f.run(['worktree', 'lock', f.legacyShell], origin);
  await assert.rejects(f.prepare(), /locked/);
  assert.equal(await has(f.legacyShell), true);
  await f.run(['worktree', 'unlock', f.legacyShell], origin);
  assert.equal(await f.prepare(), f.shell);
  assert.equal(await fs.readFile(path.join(f.shell, 'source.ts'), 'utf8'), 'dirty linked shell');
  assert.match(await f.run(['worktree', 'list', '--porcelain'], origin), /shell-source/);
});

test('interrupted relocation after rename resumes Git repair and cache migration without recloning', async (t) => {
  const f = await fixture(t); await repository(f, f.legacyShell);
  let denied = true;
  const command = async (file, args, options) => {
    if (args[0] === 'worktree' && args[1] === 'repair' && denied) throw Object.assign(new Error('simulated permission denial during repair'), { code: 'EACCES' });
    return f.owner.command(file, args, options);
  };
  await assert.rejects(f.prepare({ command }), /permission denial/);
  assert.equal(await has(path.join(f.shell, '.git')), true); assert.equal(await has(path.join(f.dataRoot, '.shell-layout.json')), true);
  denied = false; assert.equal(await f.prepare({ command }), f.shell);
  assert.equal(await has(path.join(f.dataRoot, '.shell-layout.json')), false);
});

test('a rename permission failure retains original checkout and cache, then retry completes', async (t) => {
  const f = await fixture(t); await repository(f, f.legacyShell);
  await fs.mkdir(f.legacyCache); await fs.writeFile(path.join(f.legacyCache, 'receipt'), 'retain');
  await assert.rejects(f.prepare({ rename: async (source, target) => { if (source === f.legacyShell) throw Object.assign(new Error('simulated locked directory'), { code: 'EPERM' }); return fs.rename(source, target); } }), /locked directory/);
  assert.equal(await has(path.join(f.legacyShell, '.git')), true); assert.equal(await has(f.legacyCache), true);
  await f.prepare(); assert.equal(await fs.readFile(path.join(f.cache, 'receipt'), 'utf8'), 'retain');
});

test('partial fresh clone resumes per-entry installation; cache collisions preserve both copies', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  let interrupted = false;
  await assert.rejects(f.prepare({ rename: async (source, target) => { if (path.basename(source) === 'source.ts' && !interrupted) { interrupted = true; throw new Error('simulated bootstrap interruption'); } return fs.rename(source, target); } }), /bootstrap interruption/);
  assert.equal(await has(path.join(f.dataRoot, '.shell-layout.json')), true);
  await f.prepare(); assert.equal((await f.run(['status', '--porcelain'], f.shell)).trim(), '');
  await fs.writeFile(path.join(f.cache, 'receipt'), 'new cache');
  await fs.mkdir(f.legacyCache); await fs.writeFile(path.join(f.legacyCache, 'receipt'), 'old cache');
  await f.prepare();
  assert.equal(await fs.readFile(path.join(f.cache, 'receipt'), 'utf8'), 'new cache');
  assert.equal(await fs.readFile(path.join(f.cache, 'legacy-profile', 'receipt'), 'utf8'), 'old cache');
});

test('live migration lock, Git lock and conflicting source trees stop without replacing files', async (t) => {
  const f = await fixture(t); await repository(f, f.legacyShell);
  const lock = path.join(f.dataRoot, '.shell-layout.lock');
  await fs.writeFile(lock, JSON.stringify({ pid: process.pid })); await assert.rejects(f.prepare(), /already running/); await fs.unlink(lock);
  await fs.writeFile(path.join(f.legacyShell, '.git/index.lock'), 'Git owns this');
  await assert.rejects(f.prepare(), /Git is locked/); await fs.unlink(path.join(f.legacyShell, '.git/index.lock'));
  await repository(f, f.shell); await assert.rejects(f.prepare(), /Both legacy MAGI and shell/);
  assert.equal(await has(path.join(f.legacyShell, '.git')), true); assert.equal(await has(path.join(f.shell, '.git')), true);
});

test('a dead migration owner can be recovered without signalling unrelated processes', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  await fs.mkdir(f.dataRoot);
  const child = f.owner.spawn(process.execPath, ['-e', 'process.exit(0)'], { stdio: 'ignore' });
  await new Promise((resolve) => child.once('close', resolve)); await f.owner.reap(child);
  await fs.writeFile(path.join(f.dataRoot, '.shell-layout.lock'), JSON.stringify({ pid: child.pid }));
  assert.equal(await f.prepare(), f.shell);
  assert.equal(await has(path.join(f.dataRoot, '.shell-layout.lock')), false);
});
