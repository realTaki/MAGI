import assert from 'node:assert/strict';
import test from 'node:test';
import { execFileSync, spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { reviseCheckout, trackingRef } from '@magi/launcher/runtime/revise.js';
import { Bus, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, magiLocalName, messageDelivery, sequentialMagiHandle, SHELL_MAGI_HANDLE } from '@magi/bus';
import { replaceTools } from '@magi/built-in-tools/replace.js';
import { shellTools } from '@magi/built-in-tools/shell.js';
import { ShellManager } from '@magi/built-in-tools/shellManager.js';
import { resolveToolchain, toolchainEnv } from '@magi/launcher/runtime/toolchain.js';
import { VERIFY_NPM_ARGS } from '@magi/launcher/runtime/verify.js';
import { ReplaceWorker } from '../../../packages/launcher/dist/replace.js';

const repo = path.resolve(import.meta.dirname, '../../..');
const node = process.execPath;
const npm = process.env.MAGI_NPM?.trim() || process.env.npm_execpath?.trim() || path.join(repo, 'apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js');
/** Windows bash is PowerShell 5.1; bare `node` is not a reliable native invocation. */
function nodeCommand(script: string): string {
  return process.platform === 'win32' ? `& ${JSON.stringify(node)} ${script}` : `${JSON.stringify(node)} ${script}`;
}
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
  mkdirSync(path.join(root, 'scripts'), { recursive: true });
  copyFileSync(path.join(repo, 'scripts/verify.mjs'), path.join(root, 'scripts/verify.mjs'));
  const pkg = { name: 'source-fixture', version: '1.0.0', type: 'module', scripts: { test: 'node build.mjs test', build: 'node build.mjs build', verify: 'node scripts/verify.mjs' } };
  writeFileSync(path.join(root, 'package.json'), JSON.stringify(pkg));
  writeFileSync(path.join(root, 'package-lock.json'), JSON.stringify({ name: pkg.name, version: pkg.version, lockfileVersion: 3, packages: { '': { name: pkg.name, version: pkg.version } } }));
  writeFileSync(path.join(root, '.gitignore'), 'node_modules/\ndist/\n.env\n');
  writeFileSync(path.join(root, 'source.txt'), 'upstream-v1');
  writeFileSync(path.join(root, 'removed.txt'), 'remove this source');
  writeFileSync(path.join(root, 'staged.txt'), 'original');
  writeFileSync(path.join(root, 'build.mjs'), `import{readFileSync,mkdirSync,writeFileSync}from'node:fs';import{dirname,basename}from'node:path';import{fileURLToPath}from'node:url';process.chdir(dirname(fileURLToPath(import.meta.url)));const value=readFileSync('source.txt','utf8');if(value==='INVALID')throw Error('candidate rejected');if(process.argv[2]==='test'){mkdirSync('dist',{recursive:true});writeFileSync('dist/test-env',JSON.stringify({home:process.env.MAGI_HOME,dirName:basename(process.cwd()),token:process.env.MAGI_ASP_TOKEN!==undefined,replace:process.env.MAGI_REPLACE_JOB!==undefined,role:process.env.MAGI_ROLE??''}))}else if(process.argv[2]==='ui'){mkdirSync('apps/shell/dist',{recursive:true});writeFileSync('apps/shell/dist/index.html',value)}else if(process.argv[2]==='build'){mkdirSync('dist',{recursive:true});writeFileSync('dist/proof',value)}`);
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
  assert.equal(validation.role, '');
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

test('request_replace binds the editable source to this node workspace, including after a revision switch', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-rebuild-scope-'));
  const bus = new Bus(SHELL_MAGI_HANDLE, home);
  t.after(() => { bus.close(); rmSync(home, { recursive: true, force: true }); });
  const tool = replaceTools(bus)[0];
  assert.equal(tool.name, 'request_replace');
  const { id } = JSON.parse(await tool.run({ checkout: path.join(home, 'other-node') }));
  const job = bus.board('ReplaceSelfJob').claimId('test', id);
  assert.equal(job?.input.checkout, path.join(home, 'MAGI'));
  assert.equal(job?.input.ref, undefined);
});

test('Shell tools keep startup source across revision switches without exposing it to another node', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-source-authority-'));
  const shell = new Bus(SHELL_MAGI_HANDLE, path.join(home, 'shell'));
  const other = new Bus(sequentialMagiHandle(99), path.join(home, magiLocalName(sequentialMagiHandle(99))));
  const previous = { ...process.env };
  t.after(() => {
    shell.close(); other.close();
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
    rmSync(home, { recursive: true, force: true });
  });
  process.env.MAGI_ROLE = 'shell';
  process.env.MAGI_CHECKOUT = path.join(home, 'development-source');
  process.env.MAGI_APP_CHECKOUT = path.join(home, 'shell/revisions/new');
  for (const bus of [shell, other]) {
    const { id } = JSON.parse(await replaceTools(bus)[0].run({ checkout: '/unrelated' }));
    const job = bus.board('ReplaceSelfJob').claimId('test', id);
    assert.equal(job?.input.checkout, bus === shell ? process.env.MAGI_CHECKOUT : path.join(other.workspace, 'MAGI'));
  }
});


test('the actual replace worker builds local tool edits without an upstream and sends the built revision to its launcher', async (t) => {
  let bus: Bus | undefined;
  const f = fixture(t, () => bus?.close());
  f.git('branch', '--unset-upstream');
  writeFileSync(path.join(f.root, 'source.txt'), 'worker-local-v2');
  // Source launches need not live under the Shell's persistent data workspace.
  process.env.MAGI_CHECKOUT = f.root;
  process.env.MAGI_APP_CHECKOUT = path.join(f.home, 'shell/revisions/old');
  bus = new Bus(SHELL_MAGI_HANDLE, path.join(f.home, 'node-workspace'));
  const savedSend = process.send, savedConnected = Object.getOwnPropertyDescriptor(process, 'connected');
  let outgoing;
  process.send = (message) => { outgoing = message; setImmediate(() => process.emit('message', { v: 1, type: 'shutdown' })); return true; };
  Object.defineProperty(process, 'connected', { configurable: true, value: true });
  t.after(() => { process.send = savedSend; if (savedConnected) Object.defineProperty(process, 'connected', savedConnected); else delete process.connected; });
  const worker = new ReplaceWorker(bus, { app: 'shell', script: 'build:process' }, { healthy: () => null, suspendForReplace: async () => {}, resume: async () => {} });
  const { id } = JSON.parse(await replaceTools(bus)[0].run({}));
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

test('agent bash runs the shared verify entry on a managed clone with MAGI_NODE/MAGI_NPM', async (t) => {
  const f = fixture(t);
  const workspace = mkdtempSync(path.join(tmpdir(), 'magi-managed-clone-'));
  const clone = path.join(workspace, 'MAGI');
  t.after(() => rmSync(workspace, { recursive: true, force: true }));
  execFileSync('git', ['clone', '--local', '--no-hardlinks', f.root, clone]);
  const bash = shellTools(workspace, new ShellManager())[0];
  // Windows `bash` is PowerShell, so the agent command cannot be a bash-only snippet.
  writeFileSync(path.join(workspace, 'print-toolchain.mjs'), 'process.stdout.write(`${process.env.MAGI_NODE}\\n${process.env.MAGI_NPM}`);');
  const printed = await bash.run({ command: 'node print-toolchain.mjs' });
  assert.match(printed, /^exit_code: 0\n/);
  const { node: resolvedNode, npm: resolvedNpm } = resolveToolchain();
  assert.match(printed, new RegExp(resolvedNode.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(printed, new RegExp(resolvedNpm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  writeFileSync(path.join(workspace, 'run-verify.mjs'), `import { spawnSync } from 'node:child_process';
process.chdir('MAGI');
const result = spawnSync(process.env.MAGI_NODE, [process.env.MAGI_NPM, ...${JSON.stringify([...VERIFY_NPM_ARGS])}], { stdio: 'inherit', env: process.env });
process.exit(result.status ?? 1);
`);
  const result = await bash.run({ command: 'node run-verify.mjs', timeout: 60 });
  if (!result.startsWith('exit_code: 0')) assert.fail(result);
  const validation = JSON.parse(readFileSync(path.join(clone, 'dist/test-env'), 'utf8'));
  assert.notEqual(validation.home, f.home);
  assert.equal(validation.role, '');
  assert.equal(readFileSync(path.join(clone, 'dist/proof'), 'utf8'), 'upstream-v1');
  assert.equal(readFileSync(path.join(clone, 'apps/shell/dist/index.html'), 'utf8'), 'upstream-v1');
});

test('toolchainEnv does not put . on PATH when node is an unqualified name', () => {
  const env = toolchainEnv({
    MAGI_NODE: 'node',
    MAGI_NPM: npm,
    MAGI_GIT: 'git',
    PATH: `${path.dirname(process.execPath)}${path.delimiter}/usr/bin`,
  });
  assert.equal(env.PATH?.startsWith(`.${path.delimiter}`), false);
  assert.equal(env.MAGI_NODE, 'node');
});

test('bash stays available when Node 24 cannot be resolved', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-bash-fallback-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const previous = process.env.MAGI_NODE;
  process.env.MAGI_NODE = path.join(home, 'missing-node');
  t.after(() => {
    if (previous === undefined) delete process.env.MAGI_NODE;
    else process.env.MAGI_NODE = previous;
  });
  assert.throws(() => resolveToolchain(), /did not report a version|requires Node/);
  writeFileSync(path.join(home, 'echo.mjs'), "process.stdout.write('still-here');");
  const result = await shellTools(home, new ShellManager())[0].run({ command: nodeCommand('echo.mjs') });
  assert.match(result, /exit_code: 0/);
  assert.match(result, /still-here/);
});

test('foreground bash keeps the tail of long output', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-bash-tail-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  writeFileSync(path.join(home, 'long.mjs'), `process.stdout.write(${JSON.stringify(`${"H".repeat(10_000)}\nUNIQUE_TAIL_MARKER\n`)});`);
  const result = await shellTools(home, new ShellManager())[0].run({ command: nodeCommand('long.mjs') });
  assert.match(result, /UNIQUE_TAIL_MARKER/);
  assert.match(result, /…/);
});

function failingFixture(t, beforeRemove = () => {}) {
  const f = fixture(t, beforeRemove);
  writeFileSync(path.join(f.root, 'source.txt'), 'INVALID');
  return f;
}

async function finishReplace(worker, bus, id, ms = 30_000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    await worker.poll();
    const result = bus.board('ReplaceSelfJob').result(id);
    if (result) return result;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error('replace did not finish');
}

function claimWake(bus: Bus) {
  const wake = bus.board('MessageDeliveryJob').claim('agent', (input) => bus.messages.get(input.message_id)?.contact_id !== MAGI_CONTACT_ID);
  assert.ok(wake);
  bus.board('MessageDeliveryJob').submit('agent', wake.id, { output: {} });
}

test('swap:true safety-gate failure goes through reviseCheckout and wakes the requesting chat per chat until the cap', async (t) => {
  let bus: Bus | undefined;
  const f = failingFixture(t, () => bus?.close());
  process.env.MAGI_CHECKOUT = f.root;
  bus = new Bus('@gate.magi', path.join(f.home, 'node-workspace'));
  const agentA = bus.chats.forChannel('cli', 'agent-a');
  const agentB = bus.chats.forChannel('cli', 'agent-b');
  const operatorChat = bus.chats.forChannel('cli', 'home');
  messageDelivery.setHomeChat(bus, operatorChat.id);
  const savedSend = process.send, savedConnected = Object.getOwnPropertyDescriptor(process, 'connected');
  process.send = () => { throw new Error('launcher must not switch after a failed safety gate'); };
  Object.defineProperty(process, 'connected', { configurable: true, value: true });
  t.after(() => { process.send = savedSend; if (savedConnected) Object.defineProperty(process, 'connected', savedConnected); else delete process.connected; });
  const worker = new ReplaceWorker(bus, { app: 'eva', script: 'build' }, { healthy: () => null, suspendForReplace: async () => {}, resume: async () => {} });
  async function failOnce(chatId: number) {
    const id = bus!.board('ReplaceSelfJob').publish({ checkout: f.root, swap: true, chat_id: chatId }, 'test');
    return finishReplace(worker, bus!, id);
  }
  for (let attempt = 1; attempt <= 3; attempt++) {
    const result = await failOnce(agentA.id);
    assert.equal(result.status, 'failed');
    assert.match(result.error ?? '', /candidate rejected/);
    const last = bus.messages.list(agentA.id).at(-1);
    assert.equal(last?.contact_id, SYSTEM_CONTACT_ID);
    assert.match(last!.content, new RegExp(`attempt ${attempt}/3`));
    assert.match(last!.content, /safety gate failed/);
    assert.equal(bus.messages.list(operatorChat.id).length, 0);
    claimWake(bus);
  }
  const other = await failOnce(agentB.id);
  assert.equal(other.status, 'failed');
  const fromB = bus.messages.list(agentB.id).at(-1);
  assert.match(fromB!.content, /attempt 1\/3/);
  claimWake(bus);
  const fourth = await failOnce(agentA.id);
  assert.equal(fourth.status, 'failed');
  assert.equal(bus.messages.list(agentA.id).filter((row) => row.contact_id === SYSTEM_CONTACT_ID).length, 4);
  const operator = bus.messages.list(agentA.id).at(-1);
  assert.equal(operator?.contact_id, SYSTEM_CONTACT_ID);
  assert.equal(bus.deliveries.get(operator!.id)?.enqueue, false);
  assert.match(operator!.content, /after 3 attempts/);
  const leftover = bus.board('MessageDeliveryJob').claim('agent', (input) => bus.messages.get(input.message_id)?.contact_id !== MAGI_CONTACT_ID);
  assert.equal(leftover, null);
});

test('desktop replace builds a unique package then calls host, never a node-only switch', { skip: process.platform !== 'linux' }, async (t) => {
  let bus;
  const f = fixture(t, () => bus?.close());
  process.env.MAGI_CHECKOUT = f.root;
  process.env.MAGI_PACKAGED = '1';
  writeFileSync(path.join(f.root, 'apps/shell/package.json'), JSON.stringify({ scripts: { 'build:ui': 'node ../../build.mjs ui', build: 'node package.mjs' } }));
  writeFileSync(path.join(f.root, 'apps/shell/package.mjs'), `import{writeFileSync}from'node:fs';import{join}from'node:path';const output=process.argv.find(x=>x.startsWith('--config.directories.output=')).split('=').slice(1).join('=');writeFileSync(join(output,'fixture.AppImage'),'verified-package');`);
  bus = new Bus(SHELL_MAGI_HANDLE, path.join(f.home, 'node-workspace'));
  const savedSend = process.send, savedConnected = Object.getOwnPropertyDescriptor(process, 'connected');
  let outgoing, installed;
  process.send = message => { outgoing = message; return true; };
  Object.defineProperty(process, 'connected', { configurable: true, value: true });
  t.after(() => { process.send = savedSend; if (savedConnected) Object.defineProperty(process, 'connected', savedConnected); else delete process.connected; });
  const worker = new ReplaceWorker(bus, { app: 'shell', script: 'build:process' }, {
    healthy: () => null, suspendForReplace: async () => {}, resume: async () => {},
    installDesktopUpdate: async (manifest, job) => { installed = { manifest, job }; },
  });
  const { id } = JSON.parse(await replaceTools(bus)[0].run({ desktop: true }));
  await worker.poll();
  const deadline = Date.now() + 15_000;
  while (!installed && Date.now() < deadline) {
    const result = bus.board('ReplaceSelfJob').result(id);
    if (result?.status === 'failed') assert.fail(result.error);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.equal(installed?.job, id);
  assert.equal(readFileSync(installed.manifest.installer, 'utf8'), 'verified-package');
  assert.equal(outgoing, undefined);
  await worker.poll();
  assert.equal(bus.board('ReplaceSelfJob').result(id), null, 'arming installation must not report success or failure before Electron exits');
});

test('launcher rollback copy is distinct from a safety-gate failure, uses the job chat_id, and caps per chat', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-replace-crash-'));
  const bus = new Bus('@crash.magi', home);
  t.after(() => { bus.close(); rmSync(home, { recursive: true, force: true }); });
  const agentChat = bus.chats.forChannel('cli', 'agent');
  const otherChat = bus.chats.forChannel('cli', 'other');
  const operatorChat = bus.chats.forChannel('cli', 'home');
  messageDelivery.setHomeChat(bus, operatorChat.id);
  bus.settings.set('replace.gate_failures.999', '3');
  const worker = new ReplaceWorker(bus, { app: 'eva', script: 'build' }, { healthy: () => null, suspendForReplace: async () => {}, resume: async () => {} });
  async function failOnce(chatId: number) {
    const id = bus.board('ReplaceSelfJob').publish({ checkout: path.join(home, 'absent'), swap: true, chat_id: chatId }, 'test');
    return finishReplace(worker, bus, id, 5_000);
  }
  for (let attempt = 1; attempt <= 3; attempt++) {
    const result = await failOnce(agentChat.id);
    assert.equal(result.status, 'failed');
    const last = bus.messages.list(agentChat.id).at(-1);
    assert.equal(last?.contact_id, SYSTEM_CONTACT_ID);
    assert.match(last!.content, /launcher rollback or crash/);
    assert.match(last!.content, new RegExp(`attempt ${attempt}/3`));
    assert.doesNotMatch(last!.content, /safety gate failed/);
    assert.equal(bus.messages.list(operatorChat.id).length, 0);
    claimWake(bus);
  }
  const other = await failOnce(otherChat.id);
  assert.equal(other.status, 'failed');
  assert.match(bus.messages.list(otherChat.id).at(-1)!.content, /attempt 1\/3/);
  claimWake(bus);
  const fourth = await failOnce(agentChat.id);
  assert.equal(fourth.status, 'failed');
  assert.equal(bus.messages.list(agentChat.id).filter((row) => row.contact_id === SYSTEM_CONTACT_ID).length, 4);
  const operator = bus.messages.list(agentChat.id).at(-1);
  assert.equal(operator?.contact_id, SYSTEM_CONTACT_ID);
  assert.equal(bus.deliveries.get(operator!.id)?.enqueue, false);
  assert.match(operator!.content, /after 3 attempts/);
  assert.match(operator!.content, /launcher rollback or crash/);
  const leftover = bus.board('MessageDeliveryJob').claim('agent', (input) => bus.messages.get(input.message_id)?.contact_id !== MAGI_CONTACT_ID);
  assert.equal(leftover, null);
});

test('MAGI_REPLACE_ERROR finishHandoff rolls back through the crash path', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-replace-error-'));
  const bus = new Bus('@handoff.magi', home);
  const previousJob = process.env.MAGI_REPLACE_JOB;
  const previousError = process.env.MAGI_REPLACE_ERROR;
  t.after(() => {
    if (previousJob === undefined) delete process.env.MAGI_REPLACE_JOB;
    else process.env.MAGI_REPLACE_JOB = previousJob;
    if (previousError === undefined) delete process.env.MAGI_REPLACE_ERROR;
    else process.env.MAGI_REPLACE_ERROR = previousError;
    bus.close();
    rmSync(home, { recursive: true, force: true });
  });
  const agentChat = bus.chats.forChannel('cli', 'agent');
  const operatorChat = bus.chats.forChannel('cli', 'home');
  messageDelivery.setHomeChat(bus, operatorChat.id);
  const worker = new ReplaceWorker(bus, { app: 'eva', script: 'build' }, { healthy: () => null, suspendForReplace: async () => {}, resume: async () => {} });
  const id = bus.board('ReplaceSelfJob').publish({ checkout: path.join(home, 'MAGI'), swap: true, chat_id: agentChat.id }, 'test');
  process.env.MAGI_REPLACE_JOB = String(id);
  process.env.MAGI_REPLACE_ERROR = 'launcher rolled the process back';
  const result = await finishReplace(worker, bus, id, 5_000);
  assert.equal(result.status, 'failed');
  assert.equal(result.error, 'launcher rolled the process back');
  const last = bus.messages.list(agentChat.id).at(-1);
  assert.equal(last?.contact_id, SYSTEM_CONTACT_ID);
  assert.match(last!.content, /launcher rollback or crash/);
  assert.match(last!.content, /launcher rolled the process back/);
  assert.equal(bus.messages.list(operatorChat.id).length, 0);
  assert.equal(process.env.MAGI_REPLACE_JOB, undefined);
  assert.equal(process.env.MAGI_REPLACE_ERROR, undefined);
});

test('non-swap dist build failure says build failure, not crash', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-replace-build-'));
  const bus = new Bus('@build.magi', home);
  t.after(() => { bus.close(); rmSync(home, { recursive: true, force: true }); });
  const agentChat = bus.chats.forChannel('cli', 'agent');
  const operatorChat = bus.chats.forChannel('cli', 'home');
  messageDelivery.setHomeChat(bus, operatorChat.id);
  const worker = new ReplaceWorker(bus, { app: 'eva', script: 'build' }, { healthy: () => null, suspendForReplace: async () => {}, resume: async () => {} });
  const id = bus.board('ReplaceSelfJob').publish({ checkout: path.join(home, 'absent'), swap: false, chat_id: agentChat.id }, 'test');
  const result = await finishReplace(worker, bus, id, 5_000);
  assert.equal(result.status, 'failed');
  const last = bus.messages.list(agentChat.id).at(-1);
  assert.equal(last?.contact_id, SYSTEM_CONTACT_ID);
  assert.match(last!.content, /build failure/);
  assert.doesNotMatch(last!.content, /launcher rollback or crash/);
  assert.equal(bus.messages.list(operatorChat.id).length, 0);
});

test('verify.mjs does not leak MAGI_ROLE into test or build', (t) => {
  const f = fixture(t);
  assert.equal(process.env.MAGI_ROLE, 'shell');
  const result = spawnSync(node, [npm, 'run', 'verify'], { cwd: f.root, env: process.env, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  const validation = JSON.parse(readFileSync(path.join(f.root, 'dist/test-env'), 'utf8'));
  assert.equal(validation.role, '');
});

test('verify.mjs removes temp MAGI_HOME after a failed test', (t) => {
  const f = fixture(t);
  writeFileSync(path.join(f.root, 'build.mjs'), `import { mkdirSync, writeFileSync } from 'node:fs';
mkdirSync('dist', { recursive: true });
writeFileSync('dist/test-env', process.env.MAGI_HOME ?? '');
process.exit(2);
`);
  const result = spawnSync(node, [npm, 'run', 'verify'], { cwd: f.root, env: process.env, encoding: 'utf8' });
  assert.notEqual(result.status, 0);
  const isolated = readFileSync(path.join(f.root, 'dist/test-env'), 'utf8');
  assert.notEqual(isolated, '');
  assert.equal(existsSync(isolated), false);
});
