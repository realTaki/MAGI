import assert from 'node:assert/strict';
import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { reviseCheckout, trackingRef } from '@magi/runtime/revise.js';
import { Bus, SHELL_MAGI_HANDLE } from '@magi/bus';
import { rebuildTools } from '@magi/built-in-tools/rebuild.js';
import { replaceTools } from '@magi/built-in-tools/replace.js';
import { ReplaceWorker } from '../../../packages/manager/dist/replace.js';

const repo = path.resolve(import.meta.dirname, '../../..');
const node = process.execPath;
const npm = process.env.MAGI_NPM?.trim() || process.env.npm_execpath?.trim() || path.join(repo, 'apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js');
function fixture(t, beforeRemove = () => {}) {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-local-revision-'));
  const root = path.join(home, 'MAGI');
  const previous = { ...process.env };
  Object.assign(process.env, { MAGI_HOME: home, MAGI_NODE: node, MAGI_NPM: npm, MAGI_GIT: 'git', MAGI_NODE_NAME: 'shell', MAGI_ROLE: 'shell', PATH: `${path.dirname(node)}${path.delimiter}${process.env.PATH}` });
  delete process.env.MAGI_REVISION; delete process.env.MAGI_PREVIOUS_REVISION;
  t.after(() => { beforeRemove(); for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key]; Object.assign(process.env, previous); rmSync(home, { recursive: true, force: true }); });
  mkdirSync(root);
  const git = (...args: string[]) => execFileSync('git', args, { cwd: root, encoding: 'utf8', env: { ...process.env, GIT_OPTIONAL_LOCKS: '0' } }).trim();
  git('init', '--initial-branch=main'); git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'initial');
  const pkg = { name: 'source-fixture', version: '1.0.0', type: 'module', scripts: { test: 'node build.mjs test', build: 'node build.mjs build' } };
  writeFileSync(path.join(root, 'package.json'), JSON.stringify(pkg));
  writeFileSync(path.join(root, 'package-lock.json'), JSON.stringify({ name: pkg.name, version: pkg.version, lockfileVersion: 3, packages: { '': { name: pkg.name, version: pkg.version } } }));
  writeFileSync(path.join(root, '.gitignore'), 'node_modules/\ndist/\n.env\n');
  writeFileSync(path.join(root, 'source.txt'), 'upstream-v1');
  writeFileSync(path.join(root, 'removed.txt'), 'remove this source');
  writeFileSync(path.join(root, 'staged.txt'), 'original');
  writeFileSync(path.join(root, 'build.mjs'), `import{readFileSync,mkdirSync,writeFileSync}from'node:fs';import{dirname,basename}from'node:path';import{fileURLToPath}from'node:url';process.chdir(dirname(fileURLToPath(import.meta.url)));const value=readFileSync('source.txt','utf8');if(value==='INVALID')throw Error('candidate rejected');if(process.argv[2]==='test'){mkdirSync('dist',{recursive:true});writeFileSync('dist/test-env',JSON.stringify({home:process.env.MAGI_HOME,dirName:basename(process.cwd()),token:process.env.MAGI_ASP_TOKEN!==undefined,replace:process.env.MAGI_REPLACE_JOB!==undefined}))}else if(process.argv[2]==='ui'){mkdirSync('apps/shell/dist',{recursive:true});writeFileSync('apps/shell/dist/index.html',value)}else if(process.argv[2]==='build'){mkdirSync('dist',{recursive:true});writeFileSync('dist/proof',value)}`);
  mkdirSync(path.join(root, 'apps/shell'), { recursive: true });
  writeFileSync(path.join(root, 'apps/shell/package.json'), JSON.stringify({ scripts: { 'build:ui': 'node ../../build.mjs ui' } }));
  git('add', '.'); git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture');
  git('remote', 'add', 'origin', root); git('fetch', 'origin'); git('branch', '--set-upstream-to=origin/main');
  return { home, root, git };
}

test('local rebuild includes unstaged, staged, untracked and removed source without moving the source branch/index', async (t) => {
  const f = fixture(t);
  writeFileSync(path.join(f.root, 'source.txt'), 'local-v2');
  writeFileSync(path.join(f.root, 'staged.txt'), 'staged change'); f.git('add', 'staged.txt');
  writeFileSync(path.join(f.root, 'new.txt'), 'new source'); rmSync(path.join(f.root, 'removed.txt'));
  writeFileSync(path.join(f.root, '.env'), 'fixture-only-secret');
  const before = { head: f.git('rev-parse', 'HEAD'), status: f.git('status', '--porcelain'), refs: f.git('show-ref'), index: readFileSync(path.join(f.root, '.git/index')) };
  await assert.rejects(reviseCheckout(path.join(f.root, 'apps')), /source checkout root/);
  process.env.MAGI_ASP_TOKEN = 'fixture-only-runtime-token'; process.env.MAGI_REPLACE_JOB = '99';
  const result = await reviseCheckout(f.root, undefined);
  assert.equal(result.dir, path.join(f.home, 'shell', 'revisions', result.revision));
  const validation = JSON.parse(readFileSync(path.join(result.dir, 'dist/test-env'), 'utf8'));
  assert.equal(validation.dirName, result.revision, "validation runs at the permanent revision path");
  assert.notEqual(validation.home, f.home, 'candidate tests must not open the running node data root');
  assert.equal(validation.token, false); assert.equal(validation.replace, false);
  assert.notEqual(result.revision, before.head);
  const object = path.join('.git/objects', result.revision.slice(0, 2), result.revision.slice(2));
  const originalObject = statSync(path.join(f.root, object), { bigint: true }), copiedObject = statSync(path.join(result.dir, object), { bigint: true });
  assert.equal(originalObject.dev === copiedObject.dev && originalObject.ino === copiedObject.ino, false, 'revision objects must not depend on hard links to the source filesystem');
  assert.equal(readFileSync(path.join(result.dir, 'dist/proof'), 'utf8'), 'local-v2');
  assert.equal(readFileSync(path.join(result.dir, 'apps/shell/dist/index.html'), 'utf8'), 'local-v2');
  assert.equal(readFileSync(path.join(result.dir, 'staged.txt'), 'utf8'), 'staged change');
  assert.equal(readFileSync(path.join(result.dir, 'new.txt'), 'utf8'), 'new source');
  assert.throws(() => readFileSync(path.join(result.dir, 'removed.txt')), /ENOENT/);
  assert.throws(() => readFileSync(path.join(result.dir, '.env')), /ENOENT/);
  assert.equal(f.git('rev-parse', 'HEAD'), before.head); assert.equal(f.git('show-ref'), before.refs);
  assert.equal(f.git('status', '--porcelain'), before.status); assert.deepEqual(readFileSync(path.join(f.root, '.git/index')), before.index);
});

test('explicit upstream upgrades use committed upstream code, leaving local edits untouched', async (t) => {
  const f = fixture(t); writeFileSync(path.join(f.root, 'source.txt'), 'local-v2');
  const result = await reviseCheckout(f.root, await trackingRef(f.root));
  assert.equal(result.revision, f.git('rev-parse', 'HEAD'));
  assert.equal(readFileSync(path.join(result.dir, 'dist/proof'), 'utf8'), 'upstream-v1');
  assert.equal(readFileSync(path.join(f.root, 'source.txt'), 'utf8'), 'local-v2');
});

test('a failed local build leaves the previously active revision intact', async (t) => {
  const f = fixture(t);
  const live = await reviseCheckout(f.root, await trackingRef(f.root));
  process.env.MAGI_REVISION = live.revision;
  writeFileSync(path.join(f.root, 'source.txt'), 'INVALID');
  await assert.rejects(reviseCheckout(f.root, undefined), /candidate rejected/);
  assert.equal(readFileSync(path.join(live.dir, 'dist/proof'), 'utf8'), 'upstream-v1');
  assert.equal(readFileSync(path.join(f.root, 'source.txt'), 'utf8'), 'INVALID');
  writeFileSync(path.join(f.root, 'source.txt'), 'local-v3');
  writeFileSync(path.join(f.root, 'apps/shell/package.json'), JSON.stringify({ scripts: { 'build:ui': `node -e "console.error('UI build rejected');process.exit(3)"` } }));
  await assert.rejects(reviseCheckout(f.root), /UI build rejected/);
  assert.equal(readFileSync(path.join(live.dir, 'apps/shell/dist/index.html'), 'utf8'), 'upstream-v1');
});

test('both self-rebuild tools bind the editable source to this node workspace, including after a revision switch', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-rebuild-scope-'));
  const bus = new Bus(SHELL_MAGI_HANDLE, home);
  t.after(() => { bus.close(); rmSync(home, { recursive: true, force: true }); });
  await assert.rejects(rebuildTools(bus)[0].run({ target: 'app' }), /each node replaces itself/);
  for (const tool of [...rebuildTools(bus), ...replaceTools(bus)]) {
    const { id } = JSON.parse(await tool.run({ target: 'self', checkout: path.join(home, 'other-node') }));
    const job = bus.board('ReplaceSelfJob').claimId('test', id);
    assert.equal(job?.input.checkout, path.join(home, 'MAGI'));
    assert.equal(job?.input.ref, undefined);
  }
});


test('the actual replace worker builds local tool edits without an upstream and sends the built revision to its launcher', async (t) => {
  let bus: Bus | undefined;
  const f = fixture(t, () => bus?.close());
  f.git('branch', '--unset-upstream');
  writeFileSync(path.join(f.root, 'source.txt'), 'worker-local-v2');
  bus = new Bus(SHELL_MAGI_HANDLE, f.home);
  const savedSend = process.send, savedConnected = Object.getOwnPropertyDescriptor(process, 'connected');
  let outgoing;
  process.send = (message) => { outgoing = message; setImmediate(() => process.emit('message', { v: 1, type: 'shutdown' })); return true; };
  Object.defineProperty(process, 'connected', { configurable: true, value: true });
  t.after(() => { process.send = savedSend; if (savedConnected) Object.defineProperty(process, 'connected', savedConnected); else delete process.connected; });
  const worker = new ReplaceWorker(bus, { app: 'shell', script: 'build:process' }, { healthy: () => null, suspendForReplace: async () => {}, resume: async () => {} });
  const { id } = JSON.parse(await rebuildTools(bus)[0].run({ target: 'self' }));
  await worker.poll();
  const deadline = Date.now() + 15_000;
  while (!outgoing && Date.now() < deadline) {
    const result = bus.board('ReplaceSelfJob').result(id);
    if (result?.status === 'failed') assert.fail(result.error);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.equal(outgoing?.type, 'switch'); assert.equal(outgoing?.job, id);
  assert.equal(outgoing.dir, path.join(f.home, 'shell', 'revisions', outgoing.revision));
  assert.equal(readFileSync(path.join(outgoing.dir, 'apps/shell/dist/index.html'), 'utf8'), 'worker-local-v2');
  assert.equal(readFileSync(path.join(f.root, 'source.txt'), 'utf8'), 'worker-local-v2');
  await new Promise(resolve => setImmediate(resolve));
});
