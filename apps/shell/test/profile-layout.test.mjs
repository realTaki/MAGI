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
  assert.deepEqual(profilePaths('C:\\Users\\Someone', path.win32), {
    dataRoot: 'C:\\Users\\Someone\\.magi',
    shellHome: 'C:\\Users\\Someone\\.magi\\shell',
    shell: 'C:\\Users\\Someone\\.magi\\shell\\MAGI',
    cache: 'C:\\Users\\Someone\\.magi\\shell\\cache',
  });
});

test('fresh bootstrap tolerates Electron precreating cache; repeat startup leaves source and records unchanged', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  await fs.mkdir(path.join(f.cache, 'electron'), { recursive: true });
  await fs.writeFile(path.join(f.cache, 'electron', 'receipt'), 'existing browser cache');
  assert.equal(await f.prepare(), f.shell);
  assert.equal(path.join(f.shellHome, 'MAGI'), f.shell);
  assert.equal(path.join(f.shellHome, 'cache'), f.cache);
  assert.equal(await has(path.join(f.shell, '.git')), true);
  assert.equal(await has(path.join(f.shellHome, '.git')), false);
  assert.match((await f.run(['rev-parse', '--show-toplevel'], f.shell)).trim(), /[/\\]shell[/\\]MAGI$/);
  assert.equal((await f.run(['rev-parse', '--abbrev-ref', 'HEAD'], f.shell)).trim(), 'magi/shell');
  const head = await f.run(['rev-parse', 'HEAD'], f.shell);
  assert.equal((await f.run(['status', '--porcelain'], f.shell)).trim(), '');
  await fs.writeFile(path.join(f.shell, 'source.ts'), 'dirty shell source');
  assert.equal(await f.prepare(), f.shell);
  assert.equal(await f.run(['rev-parse', 'HEAD'], f.shell), head);
  assert.equal(await fs.readFile(path.join(f.cache, 'electron', 'receipt'), 'utf8'), 'existing browser cache');
  assert.equal(await fs.readFile(path.join(f.shell, 'source.ts'), 'utf8'), 'dirty shell source');
});

test('fresh packaged startup preserves Electron directories and window state before cloning', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  for (const directory of ['electron', 'cache', 'logs', 'crash-dumps']) {
    await fs.mkdir(path.join(f.shellHome, directory), { recursive: true });
    await fs.writeFile(path.join(f.shellHome, directory, 'receipt'), directory);
  }
  for (const name of ['window-state.json', 'window-state.json.tmp']) {
    await fs.writeFile(path.join(f.shellHome, name), '{"width":1040}');
  }
  assert.equal(await f.prepare(), f.shell);
  assert.equal(await f.prepare(), f.shell);
  for (const directory of ['electron', 'cache', 'logs', 'crash-dumps']) {
    assert.equal(await fs.readFile(path.join(f.shellHome, directory, 'receipt'), 'utf8'), directory);
  }
  assert.equal(await fs.readFile(path.join(f.shellHome, 'window-state.json'), 'utf8'), '{"width":1040}');
});

test('fresh bootstrap rejects unrelated files and symlinked Electron directories without replacing them', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  await fs.mkdir(f.shellHome, { recursive: true });
  const unrelated = path.join(f.shellHome, 'personal.txt');
  await fs.writeFile(unrelated, 'preserve');
  await assert.rejects(f.prepare(), /Shell destination contains existing files/);
  assert.equal(await fs.readFile(unrelated, 'utf8'), 'preserve');
  await fs.unlink(unrelated);
  await fs.symlink(path.join(f.home, 'origin'), path.join(f.shellHome, 'electron'), 'junction');
  await assert.rejects(f.prepare(), /Shell destination contains existing files/);
  assert.equal((await fs.lstat(path.join(f.shellHome, 'electron'))).isSymbolicLink(), true);
  assert.equal(await has(path.join(f.shell, '.git')), false);
});

test('partial fresh clone resumes by renaming the staged checkout into MAGI', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  await assert.rejects(f.prepare({ rename: async () => { throw new Error('simulated bootstrap interruption'); } }), /bootstrap interruption/);
  assert.equal(await has(path.join(f.dataRoot, '.shell-layout.json')), true);
  assert.equal(await has(path.join(f.shell, '.git')), false);
  await f.prepare();
  assert.equal((await f.run(['status', '--porcelain'], f.shell)).trim(), '');
  assert.equal((await f.run(['rev-parse', '--abbrev-ref', 'HEAD'], f.shell)).trim(), 'magi/shell');
  assert.equal(await has(path.join(f.dataRoot, '.shell-layout.json')), false);
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


test('explicit MAGI data root owns clone, cache, and journals without touching the default profile', async (t) => {
  const f = await fixture(t); await repository(f, path.join(f.home, 'origin'));
  const selected = path.join(f.home, 'isolated-profile');
  const paths = profilePaths(f.home, path, selected);
  await fs.mkdir(f.dataRoot, { recursive: true });
  await fs.writeFile(path.join(f.dataRoot, 'unrelated-profile.txt'), 'preserve existing data');
  await fs.mkdir(path.join(paths.shellHome, 'electron'), { recursive: true });
  assert.equal(await f.prepare({ dataDirectory: selected }), paths.shell);
  assert.equal(await has(path.join(paths.shell, '.git')), true);
  assert.equal(await has(paths.cache), true);
  assert.equal(await has(f.shell), false);
  assert.deepEqual(await fs.readdir(f.dataRoot), ['unrelated-profile.txt']);
  assert.equal(await fs.readFile(path.join(f.dataRoot, 'unrelated-profile.txt'), 'utf8'), 'preserve existing data');
  assert.equal(await has(path.join(selected, '.shell-layout.lock')), false);
  assert.equal(await has(path.join(selected, '.shell-layout.json')), false);
  const main = await fs.readFile(new URL('../main.mjs', import.meta.url), 'utf8');
  assert.match(main, /profilePaths\(osHome, path, process\.env\.MAGI_HOME\?\.trim\(\)\)/);
  assert.match(main, /home: osHome, dataDirectory: MAGI_DATA_ROOT/);
});
