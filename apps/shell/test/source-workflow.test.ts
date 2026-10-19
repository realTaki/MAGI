import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { createLocalApi } from '../../../packages/webapp/dist/api.js';

const env = { ...process.env, GIT_AUTHOR_NAME: 'Fixture', GIT_AUTHOR_EMAIL: 'fixture@example.invalid', GIT_COMMITTER_NAME: 'Fixture', GIT_COMMITTER_EMAIL: 'fixture@example.invalid' };
const git = (cwd, ...args) => execFileSync('git', args, { cwd, env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
function fixture(t, { verified = true, managed = false } = {}) {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-source-workflow-'));
  const seed = path.join(home, 'seed'), source = path.join(home, 'source'), revision = path.join(home, 'shell/revisions/old');
  mkdirSync(seed); git(seed, 'init', '-b', 'main');
  for (const [file, text] of Object.entries({
    'package.json': JSON.stringify({ name: 'magi' }),
    'apps/shell/package.json': JSON.stringify({ name: '@magi/shell' }),
    'source.txt': 'old', '.gitignore': 'apps/shell/dist/\n',
  })) { mkdirSync(path.dirname(path.join(seed, file)), { recursive: true }); writeFileSync(path.join(seed, file), text); }
  git(seed, 'add', '.'); git(seed, 'commit', '-m', 'old');
  git(home, 'clone', seed, source);
  mkdirSync(path.dirname(revision), { recursive: true }); git(home, 'clone', '--no-hardlinks', seed, revision); git(revision, 'checkout', '--detach');
  const old = git(source, 'rev-parse', 'HEAD');
  const oldUi = path.join(revision, 'apps/shell/dist/index.html'); mkdirSync(path.dirname(oldUi), { recursive: true }); writeFileSync(oldUi, 'old UI');
  const calls = [], events = [];
  const api = createLocalApi({
    paths: { home, checkout: source, appCheckout: revision, userData: path.join(home, 'electron') },
    repository: seed, sourceVerified: verified, managed,
    tools: { node: process.execPath, npm: 'fixture-npm-cli', git: 'git', env },
    aspProcess: { probe: async () => 'offline', owned: () => false },
    emit: (event, payload) => events.push({ event, payload }), copy() {}, openExternal: async () => {},
    command: async (file, args, options) => {
      calls.push({ file, args, cwd: options.cwd });
      if (file === 'git') return git(options.cwd, ...args);
      throw new Error(`unexpected command: ${file} ${args.join(' ')}`);
    },
  });
  t.after(async () => { await api.dispose(); rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  const advance = () => { writeFileSync(path.join(seed, 'source.txt'), 'new'); git(seed, 'commit', '-am', 'new'); return git(seed, 'rev-parse', 'HEAD'); };
  const snapshot = () => ({ head: git(source, 'rev-parse', 'HEAD'), status: git(source, 'status', '--porcelain'), index: readFileSync(path.join(source, '.git/index')) });
  return { api, home, source, seed, revision, old, oldUi, calls, events, advance, snapshot };
}

test('verified source launch syncs main into the editable branch, never the running revision or Electron process', async t => {
  const f = fixture(t); git(f.source, 'checkout', '-b', 'development');
  git(f.source, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/nonexistent');
  const next = f.advance();
  const result = await f.api['runtime.syncApp']();
  assert.deepEqual(result, { synced: [{ module: 'shell', merged: true }], failed: [] });
  assert.equal(git(f.source, 'rev-parse', 'HEAD'), next);
  assert.equal(git(f.source, 'branch', '--show-current'), 'development');
  assert.equal(git(f.revision, 'rev-parse', 'HEAD'), f.old);
  assert.equal(readFileSync(f.oldUi, 'utf8'), 'old UI');
  assert.deepEqual(f.events, [], 'sync does not activate UI, backend or Electron');
  assert.ok(f.calls.every(call => call.cwd === f.source));
  assert.ok(!f.calls.some(call => ['reset', 'checkout', 'stash', 'config'].includes(call.args[0])));
});

test('source update follows its current branch and preserves source authority after a revision switch', async t => {
  const f = fixture(t), next = f.advance();
  const result = await f.api['source.update']();
  assert.equal(result.branch, 'main'); assert.equal(result.behind, 1);
  assert.equal(git(f.source, 'rev-parse', 'HEAD'), next);
  assert.equal(git(f.revision, 'rev-parse', 'HEAD'), f.old);
});

for (const kind of ['staged', 'unstaged', 'untracked', 'MERGE_HEAD', 'rebase-merge']) {
  test(`sync refuses ${kind} without fetching or altering user work`, async t => {
    const f = fixture(t); f.advance();
    if (kind === 'untracked') writeFileSync(path.join(f.source, 'new.txt'), 'keep');
    else if (kind === 'staged' || kind === 'unstaged') {
      writeFileSync(path.join(f.source, 'source.txt'), 'keep');
      if (kind === 'staged') git(f.source, 'add', 'source.txt');
    } else {
      if (kind === 'rebase-merge') git(f.source, 'checkout', '--detach');
      const marker = path.resolve(f.source, git(f.source, 'rev-parse', '--git-path', kind));
      if (kind === 'rebase-merge') mkdirSync(marker); else writeFileSync(marker, `${f.old}\n`);
    }
    const before = f.snapshot();
    const result = await f.api['runtime.syncApp']();
    assert.equal(result.failed.length, 1);
    assert.match(result.failed[0].detail, /Local changes|Git operation is in progress/);
    assert.deepEqual(f.snapshot(), before);
    assert.ok(!f.calls.some(call => call.args[0] === 'fetch'));
  });
}

test('divergent commits and detached source are preserved with actionable refusals', async t => {
  const f = fixture(t); f.advance();
  writeFileSync(path.join(f.source, 'local.txt'), 'local commit'); git(f.source, 'add', '.'); git(f.source, 'commit', '-m', 'local');
  const before = f.snapshot();
  const result = await f.api['runtime.syncApp']();
  assert.match(result.failed[0].detail, /could not fast-forward.*preserved/);
  assert.deepEqual(f.snapshot(), before);
  assert.equal(existsSync(path.join(f.source, '.git/MERGE_HEAD')), false);
  git(f.source, 'checkout', '--detach');
  assert.match((await f.api['runtime.syncApp']()).failed[0].detail, /detached HEAD/);
  assert.equal(git(f.source, 'rev-parse', 'HEAD'), before.head);
});

test('a local edit made while fetch runs is preserved and stops the merge', async t => {
  const f = fixture(t); f.advance();
  // Interpose the command to model an external edit while a real fetch runs.
  const api = createLocalApi({
    paths: { home: f.home, checkout: f.source, appCheckout: f.revision }, repository: f.seed, sourceVerified: true,
    tools: { git: 'git', env }, emit() {}, copy() {}, openExternal: async () => {},
    command: async (_file, args, { cwd }) => {
      const output = git(cwd, ...args);
      if (args[0] === 'fetch') writeFileSync(path.join(f.source, 'new-during-fetch.txt'), 'preserve');
      return output;
    },
  });
  t.after(() => api.dispose());
  const result = await api['runtime.syncApp']();
  assert.match(result.failed[0].detail, /Local changes/);
  assert.equal(git(f.source, 'rev-parse', 'HEAD'), f.old);
  assert.equal(readFileSync(path.join(f.source, 'new-during-fetch.txt'), 'utf8'), 'preserve');
});

test('unverified source cannot sync and verified source cannot retarget GitHub clones', async t => {
  const f = fixture(t, { verified: false });
  await assert.rejects(f.api['runtime.syncApp'](), /No verified editable MAGI source/);
  assert.deepEqual(f.calls, []);
  const source = fixture(t);
  await assert.rejects(source.api['github.connect'](), /requires an app-managed installation/);
  assert.deepEqual(source.calls, []);
});

test('managed installation sync also targets editable source rather than its active revision', async t => {
  const f = fixture(t, { verified: false, managed: true }), next = f.advance();
  const result = await f.api['runtime.syncApp']();
  assert.equal(result.failed.length, 0);
  assert.equal(git(f.source, 'rev-parse', 'HEAD'), next);
  assert.equal(git(f.revision, 'rev-parse', 'HEAD'), f.old);
});

test('a runtime revision cannot become editable source just by carrying the verified flag', async t => {
  const f = fixture(t);
  const api = createLocalApi({
    paths: { home: f.home, checkout: f.revision }, repository: f.seed, sourceVerified: true,
    tools: { git: 'git', env }, emit() {}, copy() {}, openExternal: async () => {},
  });
  t.after(() => api.dispose());
  await assert.rejects(api['runtime.syncApp'](), /Runtime revisions cannot be synchronized/);
  assert.equal(git(f.revision, 'rev-parse', 'HEAD'), f.old);
});

test('changed source identity is rejected before a fetch or build', async t => {
  const f = fixture(t);
  writeFileSync(path.join(f.source, 'package.json'), JSON.stringify({ name: 'other-project' }));
  await assert.rejects(f.api['runtime.syncApp'](), /changed identity/);
  assert.ok(f.calls.every(call => call.args[0] === 'rev-parse'));
});
