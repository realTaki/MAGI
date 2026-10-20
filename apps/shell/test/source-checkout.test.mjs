import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { resolveShellCheckout } from '../source-checkout.mjs';

const env = { ...process.env, GIT_AUTHOR_NAME: 'Fixture', GIT_AUTHOR_EMAIL: 'fixture@example.invalid', GIT_COMMITTER_NAME: 'Fixture', GIT_COMMITTER_EMAIL: 'fixture@example.invalid' };
const git = (cwd, ...args) => execFileSync('git', args, { cwd, env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
function fixture(t) {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-source-root-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const root = path.join(home, 'MAGI'); mkdirSync(root);
  git(root, 'init', '-b', 'main');
  for (const [file, text] of Object.entries({
    'package.json': JSON.stringify({ name: 'magi' }),
    'apps/shell/package.json': JSON.stringify({ name: '@magi/shell' }),
    'apps/shell/main.mjs': '// Electron entry',
    'apps/eva/eva.mjs': '// launcher',
    'packages/launcher/composition/child.ts': '// node',
  })) { mkdirSync(path.dirname(path.join(root, file)), { recursive: true }); writeFileSync(path.join(root, file), text); }
  git(root, 'add', '.'); git(root, 'commit', '-m', 'MAGI source');
  const calls = [];
  const resolve = (extra = {}) => resolveShellCheckout({
    isPackaged: false, shellDirectory: path.join(root, 'apps/shell'), git: 'git', env,
    command: async (file, args, { cwd }) => { assert.equal(file, 'git'); calls.push(args); return git(cwd, ...args); },
    prepareManaged: () => { throw new Error('source must not prepare an installation'); }, ...extra,
  });
  return { root, home, calls, resolve };
}

test('source launch resolves its own tracked root without cwd or environment overrides, preserving dirty files', async t => {
  const f = fixture(t);
  writeFileSync(path.join(f.root, 'local.txt'), 'local work');
  const before = git(f.root, 'status', '--porcelain');
  const index = readFileSync(path.join(f.root, '.git/index'));
  assert.deepEqual(await f.resolve(), { checkout: realpathSync(f.root), managed: false, sourceVerified: true });
  assert.equal(git(f.root, 'status', '--porcelain'), before);
  assert.deepEqual(readFileSync(path.join(f.root, '.git/index')), index);
  assert.ok(f.calls.every(args => ['rev-parse', 'symbolic-ref', 'ls-files'].includes(args[0])));
});

test('source worktrees and explicit development overrides resolve their exact Git roots', async t => {
  const f = fixture(t), worktree = path.join(f.home, 'worktree');
  git(f.root, 'worktree', 'add', '-b', 'development', worktree);
  assert.deepEqual(await f.resolve({ shellDirectory: path.join(worktree, 'apps/shell') }), { checkout: realpathSync(worktree), managed: false, sourceVerified: true });
  assert.equal((await f.resolve({ override: ` ${worktree} ` })).checkout, realpathSync(worktree));
});

test('packaged launch keeps managed authority and ignores development overrides', async t => {
  const f = fixture(t);
  assert.deepEqual(await f.resolve({ isPackaged: true, override: '/invalid', prepareManaged: async () => '/managed/shell/MAGI' }), {
    checkout: '/managed/shell/MAGI', managed: true, sourceVerified: false,
  });
  assert.equal(f.calls.length, 0);
});

test('nested paths, unrelated repositories, missing tracked source and runtime revisions are rejected', async t => {
  const f = fixture(t);
  await assert.rejects(f.resolve({ override: path.join(f.root, 'apps') }), /Git checkout root/);
  writeFileSync(path.join(f.root, 'package.json'), JSON.stringify({ name: 'another-project' }));
  await assert.rejects(f.resolve(), /Not a MAGI source checkout/);
  git(f.root, 'restore', 'package.json');
  git(f.root, 'rm', '--cached', 'apps/eva/eva.mjs');
  await assert.rejects(f.resolve());
  git(f.root, 'restore', '--staged', 'apps/eva/eva.mjs');
  git(f.root, 'checkout', '--detach');
  await assert.rejects(f.resolve(), /source is detached/);
  git(f.root, 'checkout', 'main');
  const revision = path.join(f.home, 'shell/revisions/abc');
  mkdirSync(path.dirname(revision), { recursive: true });
  git(f.root, 'clone', f.root, revision); // Even a revision with an attached branch is not source.
  await assert.rejects(f.resolve({ override: revision }), /Runtime revision is not editable source/);
});

test('tracked source entries cannot redirect into another tree', { skip: process.platform === 'win32' }, async t => {
  const f = fixture(t), file = path.join(f.root, 'apps/shell/main.mjs');
  const outside = path.join(f.home, 'outside.mjs'); writeFileSync(outside, '// other tree');
  rmSync(file); symlinkSync(outside, file);
  await assert.rejects(f.resolve(), /must not redirect/);
});
