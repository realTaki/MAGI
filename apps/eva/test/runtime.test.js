import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, rmdir, mkdir, symlink, access, realpath, lstat, readdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { EvaRuntime, validateName, redact } from '../runtime.js';
import { readEvaPreset, resolveDmEntry, resolveMemoryEntry, resolveBotModeEntry, resolveSubconsciousEntry, resolveHistoryEntry } from '../child-host.js';

const fake = fileURLToPath(new URL('./fake-cli.js', import.meta.url));
const runtime = { executable: process.execPath, cli: fake, runtimeDir: '/unused', supportDir: '/unused' };
async function fixture(t, options) {
  const tempRoot = await realpath(tmpdir());
  const base = await mkdtemp(join(tempRoot, 'eva-unit-'));
  const manager = new EvaRuntime(runtime, { homeRoot: base, startupTimeout: 3000, stopTimeout: 1000, ...options });
  t.after(async () => {
    await manager.dispose();
    assert.ok(resolve(base).startsWith(join(tempRoot, 'eva-unit-')));
    await rm(base, { recursive: true });
  });
  return { base, manager };
}

test('node names cannot escape the runtime root', () => {
  for (const name of [undefined, null, 123, {}, '../other', '/tmp/other', '', 'ABC', 'a/b', 'a'.repeat(33)]) assert.throws(() => validateName(name));
  assert.equal(validateName('melchior'), 'melchior');
  assert.equal(validateName('eva-000'), 'eva-000');
});

test('reserved user handle fails before runtime or filesystem mutation', async t => {
  const { base, manager } = await fixture(t);
  const before = await readdir(base);
  assert.throws(() => validateName('user'), /reserved/);
  assert.throws(() => manager.paths('user'), /reserved/);
  assert.equal(manager.nodes.size, 0);
  assert.equal(manager.pending.size, 0);
  assert.deepEqual(await readdir(base), before);
});

test('DM binding reattaches existing processes and cleanly detaches across independent reload', async t => {
  const { manager } = await fixture(t);
  await manager.start(undefined, 'eva-000');
  const owned = [...manager.nodes.values()][0];
  const calls = [], closed = [];
  const attach = (channel, handle) => { calls.push({ channel, handle }); return () => closed.push(handle); };
  const release = manager.bindDm(attach);
  assert.deepEqual(calls, [{ channel: owned.child, handle: 'eva-000' }]);
  assert.throws(() => manager.bindDm(attach), /Duplicate/);
  release(); release();
  assert.deepEqual(closed, ['eva-000']);
  assert.equal(owned.child.connected, true, 'DM unload does not stop the Bot');
  const next = manager.bindDm(attach);
  assert.equal(calls.length, 2);
  await manager.stop(undefined, 'eva-000');
  assert.deepEqual(closed, ['eva-000', 'eva-000']);
  next();
  assert.equal(closed.length, 2, 'finished process attachment is not disposed twice');
});

test('diagnostics do not expose Web login URLs', () => {
  const safe = redact('dsh web: http://127.0.0.1:1234/?token=secret#fragment api_key=topsecret');
  assert.ok(!safe.includes('secret') && !safe.includes('fragment'));
});

test('start is idempotent, status is live, stop releases the lock, and restart works', async (t) => {
  const { base, manager } = await fixture(t);
  const [a, b] = await Promise.all([manager.start(base, 'eva-000'), manager.start(base, 'eva-000')]);
  assert.equal(a.pid, b.pid);
  assert.equal(a.status, 'running');
  assert.equal(a.handle, 'eva-000');
  assert.equal(a.name, a.handle);
  assert.equal(a.profile, 'eva-000');
  assert.equal(a.workspace, join(base, '.dsh-eva-000'));
  assert.equal(a.home, join(base, '.dsh-eva-000'));
  assert.equal(a.cwd, a.home);
  assert.equal(a.cwd, a.workspace);
  assert.equal(a.defaultAgentPreset, 'bot-mode');
  const nodeConfig = join(a.home, 'eva', 'node.json');
  const marker = await readFile(nodeConfig, 'utf8');
  assert.deepEqual(JSON.parse(marker), { role: 'eva-child' });
  assert.equal((await manager.status(base, 'eva-000')).pid, a.pid);
  const other = new EvaRuntime(runtime, { homeRoot: base });
  t.after(() => other.dispose());
  await assert.rejects(other.start(base, 'eva-000'), /locked/);
  const stopped = await manager.stop(base, 'eva-000');
  assert.equal(stopped.status, 'stopped');
  assert.deepEqual((await manager.status(base)).nodes, [stopped], 'stopped nodes remain listed');
  await assert.rejects(access(join(base, '.dsh-eva-000/eva/owner.lock')));
  assert.equal(await readFile(nodeConfig, 'utf8'), marker, 'stopping retains the child role');
  const next = await manager.start(base, 'eva-000');
  assert.notEqual(next.pid, a.pid);
  assert.equal(await readFile(nodeConfig, 'utf8'), marker, 'restart reuses the persistent child role');
  assert.equal((await manager.stop(base, 'eva-000')).status, 'stopped');
});

test('managed child DM attachment owns the spawned IPC lifetime and startup overlay', async t => {
  const attachments = [];
  const { base, manager } = await fixture(t, { dmAttach(channel, handle) {
    assert.equal(channel.connected, true);
    assert.equal(typeof channel.send, 'function');
    for (const event of ['message', 'error', 'exit', 'close']) assert.ok(channel.listenerCount(event) > 0, `runtime installs ${event} listeners before DM attachment`);
    const entry = { channel, handle, closed: 0 };
    attachments.push(entry);
    const receive = () => {};
    channel.on('message', receive);
    return () => { entry.closed++; channel.off('message', receive); };
  } });
  const [first, duplicate] = await Promise.all([manager.start(base, 'eva-000'), manager.start(base, 'eva-000')]);
  assert.equal(first.pid, duplicate.pid);
  assert.equal(attachments.length, 1, 'idempotent starts attach only the actual spawned process');
  assert.equal(attachments[0].handle, 'eva-000');
  assert.equal(attachments[0].channel.pid, first.pid);
  const args = attachments[0].channel.spawnargs;
  const patch = JSON.parse(await readFile(args[args.indexOf('--patch') + 1], 'utf8'));
  assert.equal(patch[0].insert.length, 7);
  assert.deepEqual(patch[0].insert[2], { id: 'magi-memory-child', name: resolveMemoryEntry(), config: {} });
  assert.deepEqual(patch[0].insert[1], { id: 'magi-dm-child', name: resolveDmEntry(), config: {} });
  assert.equal(patch[0].insert[0].id, 'eva-child');
  assert.equal(patch[0].insert[0].name, fileURLToPath(new URL('../child.js', import.meta.url)));
  assert.equal(patch[0].insert[0].config.profile, 'eva-000');
  assert.equal(patch[0].insert.some(row => row.id === 'eva-dm-child'), false);
  const preset = await readEvaPreset();
  assert.deepEqual(patch[0].insert[4], { id: 'eva-agent-preset', name: 'cordis:include',
    config: { path: join(first.home, 'profiles', '.eva-assets', first.profile, `preset-eva-${preset.digest}.yml`) } });
  await manager.stop(base, 'eva-000');
  assert.equal(attachments[0].closed, 1, 'process exit releases DM listener ownership exactly once');
  await manager.start(base, 'eva-000');
  assert.equal(attachments.length, 2);
  assert.notEqual(attachments[0].channel, attachments[1].channel);
  assert.equal(attachments[1].handle, 'eva-000');
  await manager.dispose();
  assert.deepEqual(attachments.map(entry => entry.closed), [1, 1]);
});

test('failed readiness also disposes the exact spawned DM attachment', async t => {
  const attachments = [];
  const { base, manager } = await fixture(t, { dmAttach(channel, handle) {
    const entry = { channel, handle, closed: 0 }; attachments.push(entry);
    return () => { entry.closed++; };
  } });
  await assert.rejects(manager.start(base, 'failure'), /before readiness/);
  assert.equal(attachments.length, 1);
  assert.equal(attachments[0].handle, 'failure');
  assert.equal(attachments[0].closed, 1);
  await assert.rejects(access(manager.paths('failure').lock), { code: 'ENOENT' });
  await manager.dispose();
  assert.equal(attachments[0].closed, 1, 'later Host disposal does not double-close an exited child');
});

test('managed child preview IPC is main-bound, bounded, isolated, and never publishes credentials', async (t) => {
  const { base, manager } = await fixture(t);
  const ready = await manager.start(base, 'eva-000');
  const managed = manager.nodes.get(manager.paths('eva-000').home);
  const send = message => managed.child.emit('message', message);
  send({ type: 'eva-preview', mainSessionId: 'unrelated', preview: { state: 'message', text: 'wrong main' } });
  assert.deepEqual((await manager.status(base, 'eva-000')).preview, { state: 'unavailable' });
  send({ type: 'eva-preview', mainSessionId: ready.mainSessionId,
    preview: { state: 'message', text: 'child latest\n message', token: 'private-secret', launchUrl: 'private-secret' }, token: 'private-secret' });
  const snapshot = await manager.status(base, 'eva-000');
  assert.deepEqual(snapshot.preview, { state: 'message', text: 'child latest message' });
  assert.ok(!JSON.stringify(snapshot).includes('private-secret'));
  await manager.stop(base, 'eva-000');
  send({ type: 'eva-preview', mainSessionId: ready.mainSessionId, preview: { state: 'message', text: 'late update' } });
  assert.deepEqual((await manager.status(base, 'eva-000')).preview, snapshot.preview, 'stopped nodes retain only last known preview');
});

for (const handle of ['wrong-cwd', 'missing-cwd', 'missing-preset']) {
  test(`readiness refuses missing or inconsistent isolation facts: ${handle}`, async (t) => {
    const { base, manager } = await fixture(t);
    await assert.rejects(manager.start(base, handle), /readiness facts/);
    await assert.rejects(access(manager.paths(handle).lock), { code: 'ENOENT' });
  });
}

test('readiness reports the saved user default instead of forcing EVA', async (t) => {
  const { base, manager } = await fixture(t);
  const node = await manager.start(base, 'user-standard');
  assert.equal(node.defaultAgentPreset, 'standard');
  assert.equal(node.cwd, node.home);
});

test('paths synchronously describe handle-global home, workspace, profile, and state', async (t) => {
  const { base, manager } = await fixture(t);
  assert.deepEqual(manager.paths('melchior'), {
    home: join(base, '.dsh-melchior'),
    workspace: join(base, '.dsh-melchior'),
    profile: 'melchior',
    profileDir: join(base, '.dsh-melchior/profiles/melchior'),
    state: join(base, '.dsh-melchior/eva'),
    lock: join(base, '.dsh-melchior/eva/owner.lock'),
    handle: 'melchior',
  });
  assert.throws(() => manager.paths());
});

test('automatic handles begin at eva-000 and reserve directories after stop', async (t) => {
  const { base, manager } = await fixture(t);
  const first = await manager.start(base);
  assert.equal(first.handle, 'eva-000');
  assert.equal(first.name, first.handle);
  await manager.stop(base, first.handle);
  const second = await manager.start(base);
  assert.equal(second.handle, 'eva-001');
});

test('automatic allocation scans only direct home entries and reuses gaps', async (t) => {
  const { base, manager } = await fixture(t);
  const occupiedHome = join(base, '.dsh-eva-000');
  await mkdir(occupiedHome);
  // A nested root-shaped entry must not reserve a user-wide handle.
  await mkdir(join(base, 'unrelated/.dsh-eva-001'), { recursive: true });
  assert.equal((await manager.start(base)).handle, 'eva-001');
  await rmdir(occupiedHome);
  assert.equal((await manager.start(base)).handle, 'eva-000');
});

test('automatic allocation skips occupied file and symlink roots without following them', async (t) => {
  const { base, manager } = await fixture(t);
  await writeFile(join(base, '.dsh-eva-000'), 'occupied file');
  await mkdir(join(base, 'target'));
  await symlink(join(base, 'target'), join(base, '.dsh-eva-001'));
  await symlink(join(base, 'missing-target'), join(base, '.dsh-eva-002'));
  assert.equal((await manager.start(base)).handle, 'eva-003');
  assert.equal(await readFile(join(base, '.dsh-eva-000'), 'utf8'), 'occupied file');
  assert.deepEqual(await readdir(join(base, 'target')), []);
});

test('allocation and start use only the root workspace without creating a nested workspace', async (t) => {
  const { base, manager } = await fixture(t);
  const handle = await manager.allocateHandle();
  const paths = manager.paths(handle);
  assert.ok((await lstat(paths.home)).isDirectory());
  assert.deepEqual(await readdir(paths.home), []);
  await manager.start(base, handle);
  assert.equal(paths.workspace, paths.home);
  assert.ok((await lstat(paths.workspace)).isDirectory());
  await assert.rejects(access(join(paths.home, 'workspace')), { code: 'ENOENT' });
});

test('a fresh start does not create or require Documents', async (t) => {
  const { base, manager } = await fixture(t);
  await assert.rejects(access(join(base, 'Documents')), { code: 'ENOENT' });
  const result = await manager.start(base);
  assert.equal(result.workspace, result.home);
  await assert.rejects(access(join(result.home, 'workspace')), { code: 'ENOENT' });
  await assert.rejects(access(join(base, 'Documents')), { code: 'ENOENT' });
});

test('concurrent managers atomically allocate distinct automatic handles', async (t) => {
  const { base, manager } = await fixture(t);
  const other = new EvaRuntime(runtime, { homeRoot: base, startupTimeout: 3000, stopTimeout: 1000 });
  try {
    const [first, second] = await Promise.all([manager.start(base), other.start(base)]);
    assert.deepEqual([first.handle, second.handle].sort(), ['eva-000', 'eva-001']);
    assert.notEqual(first.pid, second.pid);
    for (const [controller, managed, external] of [[manager, first, second], [other, second, first]]) {
      const { nodes } = await controller.status(base);
      assert.deepEqual(nodes.map((node) => node.handle), ['eva-000', 'eva-001']);
      assert.deepEqual(nodes.find((node) => node.handle === managed.handle), managed, 'managed readiness wins over inventory');
      const offline = nodes.find((node) => node.handle === external.handle);
      assert.equal(offline.status, 'not-started', 'external liveness is not inferred');
      assert.equal(offline.pid, null);
      assert.equal(offline.origin, null);
      assert.equal(offline.mainSessionId, null);
      assert.equal(controller.launchUrl(external.handle), undefined);
      assert.equal(controller.nodes.size, 1, 'discovery does not adopt another manager’s process');
    }
  } finally {
    // The fixture's earlier after-hook removes their shared home. Stop this
    // second manager first, even on assertion failure, before that hook runs.
    await other.dispose();
  }
});

test('explicit handles have the same identity across caller workspaces', async (t) => {
  const { base, manager } = await fixture(t);
  const caller = join(base, 'other-caller');
  await mkdir(caller);
  const [first, second] = await Promise.all([manager.start(base, 'melchior'), manager.start(caller, 'melchior')]);
  assert.equal(first.pid, second.pid);
  assert.equal(first.handle, 'melchior');
  assert.equal(first.name, 'melchior');
  assert.equal(first.profile, 'melchior');
  assert.equal(first.home, join(base, '.dsh-melchior'));
  assert.equal(first.workspace, join(base, '.dsh-melchior'));
  assert.equal(first.profileDir, join(base, '.dsh-melchior/profiles/melchior'));
  assert.equal((await manager.status(caller, 'melchior')).pid, first.pid);
  assert.deepEqual((await manager.status(caller)).nodes.map((node) => node.handle), ['melchior']);
  const other = new EvaRuntime(runtime, { homeRoot: base });
  t.after(() => other.dispose());
  const inventory = await other.status(caller);
  assert.deepEqual(inventory.nodes.map((node) => [node.handle, node.status]), [['melchior', 'not-started']]);
  assert.deepEqual(await other.status(caller, 'melchior'), inventory.nodes[0]);
  assert.equal(other.nodes.size, 0);
  await assert.rejects(other.start(caller, 'melchior'), /locked/);
  assert.equal((await manager.stop(caller, 'melchior')).status, 'stopped');
  assert.equal((await manager.status(base, 'melchior')).status, 'stopped');
});

test('status without a handle lists owned nodes and stop requires a handle', async (t) => {
  const { base, manager } = await fixture(t);
  assert.deepEqual(await manager.status(base), { nodes: [] });
  await assert.rejects(manager.stop(base), /handle|required/i);
  const started = await manager.start(base, 'eva-000');
  const { nodes } = await manager.status(base);
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].handle, started.handle);
  assert.equal(nodes[0].name, started.handle);
  assert.equal(nodes[0].pid, started.pid);
});

test('a new manager reconstructs numbered and named inventory after stop and dispose', async (t) => {
  const { base, manager } = await fixture(t);
  await manager.start(base, 'melchior');
  await manager.start(base, 'eva-000');
  await manager.stop(base, 'eva-000');
  await manager.dispose();
  assert.deepEqual((await manager.status(base)).nodes.map((node) => node.status), ['stopped', 'stopped']);
  const restored = new EvaRuntime(runtime, { homeRoot: base });
  t.after(() => restored.dispose());
  const { nodes } = await restored.status(base);
  assert.deepEqual(nodes.map((node) => node.handle), ['eva-000', 'melchior']);
  for (const node of nodes) {
    assert.equal(node.status, 'not-started');
    assert.equal(node.name, node.handle);
    assert.equal(node.home, manager.paths(node.handle).home);
    assert.equal(node.workspace, node.home);
    assert.equal(node.profile, node.handle);
    assert.equal(node.profileDir, manager.paths(node.handle).profileDir);
    for (const field of ['pid', 'cwd', 'origin', 'workspaceId', 'mainSessionId', 'defaultAgentPreset', 'exitCode', 'exitSignal']) {
      assert.equal(node[field], null, field);
    }
    assert.deepEqual(await restored.status(base, node.handle), node);
    assert.equal(restored.launchUrl(node.handle), undefined);
  }
  assert.equal(restored.nodes.size, 0);
  assert.deepEqual(await restored.status(base, 'unknown'), { handle: 'unknown', name: 'unknown', status: 'not-started' });
});

test('inventory recognizes legacy v1 and v2 main Session mappings without creating role markers', async (t) => {
  const { base, manager } = await fixture(t);
  for (const [handle, version] of [['casper', 1], ['balthasar', 2]]) {
    const paths = manager.paths(handle);
    await mkdir(paths.state, { recursive: true });
    const mapping = { version, state: 'ready', profile: handle, workspace: version === 1 ? join(paths.home, 'workspace') : paths.home,
      ...(version === 2 ? { originalWorkspace: join(paths.home, 'workspace') } : {}), sessionId: `session-${handle}` };
    await writeFile(join(paths.state, 'main-session.json'), JSON.stringify(mapping), { mode: 0o600 });
  }
  const { nodes } = await manager.status(base);
  assert.deepEqual(nodes.map((node) => [node.handle, node.status]), [['balthasar', 'not-started'], ['casper', 'not-started']]);
  for (const node of nodes) {
    assert.equal(node.mainSessionId, null, 'inventory does not advertise a live Session');
    assert.deepEqual(await manager.status(base, node.handle), node);
    await assert.rejects(access(join(manager.paths(node.handle).state, 'node.json')), { code: 'ENOENT' });
  }
  assert.equal(manager.nodes.size, 0);
});

test('inventory ignores unrelated, malformed, nested, and symlink homes, state, and files', async (t) => {
  const { base, manager } = await fixture(t);
  const marker = JSON.stringify({ role: 'eva-child' });
  async function state(handle) {
    const paths = manager.paths(handle);
    await mkdir(paths.state, { recursive: true });
    return paths.state;
  }
  await writeFile(join(await state('valid'), 'node.json'), marker);
  await mkdir(join(base, 'unrelated/.dsh-nested/eva'), { recursive: true });
  await writeFile(join(base, 'unrelated/.dsh-nested/eva/node.json'), marker);
  await mkdir(join(base, '.dsh-INVALID/eva'), { recursive: true });
  await writeFile(join(base, '.dsh-INVALID/eva/node.json'), marker);
  await mkdir(manager.paths('empty').home);
  await writeFile(manager.paths('file').home, marker);
  await writeFile(join(await state('wrong-role'), 'node.json'), JSON.stringify({ role: 'parent' }));
  await writeFile(join(await state('corrupt'), 'node.json'), '{invalid');
  await writeFile(join(await state('oversized'), 'node.json'), `${marker}${' '.repeat(4096)}`);
  await mkdir(join(await state('directory-marker'), 'node.json'));
  for (const [handle, mapping] of [
    ['wrong-profile', { version: 1, profile: 'someone-else', sessionId: 'session-valid' }],
    ['wrong-version', { version: 3, profile: 'wrong-version', sessionId: 'session-valid' }],
    ['wrong-session', { version: 2, profile: 'wrong-session', sessionId: '../session-invalid' }],
  ]) await writeFile(join(await state(handle), 'main-session.json'), JSON.stringify(mapping));
  const target = join(base, 'target');
  await mkdir(join(target, 'eva'), { recursive: true });
  await writeFile(join(target, 'eva/node.json'), marker);
  await writeFile(join(target, 'eva/main-session.json'), JSON.stringify({ version: 1, profile: 'linked-mapping', sessionId: 'session-valid' }));
  await symlink(target, manager.paths('linked-home').home);
  await symlink(join(base, 'missing'), manager.paths('broken-home').home);
  await mkdir(manager.paths('linked-state').home);
  await symlink(join(target, 'eva'), manager.paths('linked-state').state);
  await symlink(join(target, 'eva/node.json'), join(await state('linked-marker'), 'node.json'));
  await symlink(join(target, 'eva/main-session.json'), join(await state('linked-mapping'), 'main-session.json'));
  // Even unsafe/unavailable profiles and credentials are irrelevant to inventory.
  await symlink(join(base, 'missing-profile'), join(manager.paths('valid').home, 'profiles'));
  await symlink(join(base, 'missing-credentials'), join(manager.paths('valid').state, 'web.json'));
  assert.deepEqual((await manager.status(base)).nodes.map((node) => node.handle), ['valid']);
  for (const handle of ['linked-home', 'broken-home', 'linked-state', 'linked-marker', 'linked-mapping', 'wrong-profile', 'wrong-version', 'wrong-session']) {
    assert.deepEqual(await manager.status(base, handle), { handle, name: handle, status: 'not-started' });
  }
  const linkedRoot = join(base, 'linked-root');
  await symlink(base, linkedRoot);
  const other = new EvaRuntime(runtime, { homeRoot: linkedRoot });
  assert.deepEqual(await other.status(base), { nodes: [] });
  assert.deepEqual(await other.status(base, 'valid'), { handle: 'valid', name: 'valid', status: 'not-started' });
  await other.dispose();
  assert.equal(manager.nodes.size, 0);
});

test('inventory does not spawn, signal, migrate, or modify persisted files', async (t) => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('offline');
  await mkdir(paths.state, { recursive: true });
  const files = new Map([
    ['node.json', JSON.stringify({ role: 'eva-child' })],
    ['main-session.json', '{invalid-unused-mapping'],
    ['owner.lock', JSON.stringify({ ownerPid: process.pid, token: 'not-owned' })],
    ['migration.pending.json', '{}'],
    ['workspace-relocation.pending.json', '{}'],
  ]);
  for (const [name, content] of files) await writeFile(join(paths.state, name), content);
  const rootEntries = await readdir(base);
  const homeEntries = await readdir(paths.home);
  const stateEntries = await readdir(paths.state);
  const before = new Map();
  for (const name of files.keys()) before.set(name, await lstat(join(paths.state, name)));
  manager.runtime = new Proxy({}, { get() { assert.fail('inventory must not access the spawn runtime'); } });
  t.mock.method(manager, 'signalNode', () => assert.fail('inventory must not signal a process'));
  const first = await manager.status(base, 'offline');
  assert.equal(first.status, 'not-started');
  assert.deepEqual((await manager.status(base)).nodes, [first]);
  assert.equal(manager.nodes.size, 0);
  assert.equal(manager.pending.size, 0);
  assert.equal(manager.launchUrl('offline'), undefined);
  await manager.dispose();
  assert.deepEqual(await readdir(base), rootEntries);
  assert.deepEqual(await readdir(paths.home), homeEntries);
  assert.deepEqual(await readdir(paths.state), stateEntries);
  for (const [name, content] of files) {
    const path = join(paths.state, name);
    assert.equal(await readFile(path, 'utf8'), content);
    const after = await lstat(path);
    for (const field of ['ino', 'size', 'mode', 'mtimeMs', 'ctimeMs']) assert.equal(after[field], before.get(name)[field], `${name}: ${field}`);
  }
});

test('legacy caller .eva directories are ignored rather than reused', async (t) => {
  const { base, manager } = await fixture(t);
  const { writeFile, readFile } = await import('node:fs/promises');
  const legacy = join(base, '.eva/nodes/eva-000');
  await mkdir(join(legacy, 'home'), { recursive: true });
  await mkdir(join(legacy, 'workspace'));
  await writeFile(join(legacy, 'owner.lock'), 'legacy owner');
  const result = await manager.start(base);
  assert.equal(result.handle, 'eva-000');
  assert.equal(result.home, join(base, '.dsh-eva-000'));
  assert.equal(result.workspace, join(base, '.dsh-eva-000'));
  assert.equal(await readFile(join(legacy, 'owner.lock'), 'utf8'), 'legacy owner');
});

test('an incomplete offline migration cannot boot a partially copied profile', async (t) => {
  const { base, manager } = await fixture(t);
  const { writeFile } = await import('node:fs/promises');
  const paths = manager.paths('eva-000');
  await mkdir(paths.state, { recursive: true });
  await writeFile(join(paths.state, 'migration.pending.json'), '{}');
  await assert.rejects(manager.start(base, 'eva-000'), /incomplete migration/);
  await assert.rejects(access(paths.lock));
  assert.equal(manager.nodes.size, 0);
});

test('a helper pending marker blocks startup and releases the owner lock before patching', async (t) => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  await mkdir(paths.state, { recursive: true });
  await writeFile(join(paths.state, 'workspace-relocation.pending.json'), '{}');
  await assert.rejects(manager.start(base, 'eva-000'), /incomplete workspace relocation/);
  await assert.rejects(access(paths.lock), { code: 'ENOENT' });
  assert.deepEqual((await readdir(paths.state)).sort(), ['node.json', 'workspace-relocation.pending.json']);
  assert.deepEqual(JSON.parse(await readFile(join(paths.state, 'node.json'), 'utf8')), { role: 'eva-child' });
  assert.equal(manager.nodes.size, 0);
});

test('EVA startup patch preserves older installation-only, workspace and Creator patches', async (t) => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  await mkdir(paths.state, { recursive: true });
  const installationId = createHash('sha256').update(dirname(fileURLToPath(new URL('../runtime.js', import.meta.url)))).digest('hex').slice(0, 16);
  const oldPatch = join(paths.state, `startup-${installationId}.patch.yml`);
  const oldContent = JSON.stringify([{ insert: [{ id: 'eva-child', config: {
    workspace: join(base, 'Documents/deepseek-harness/eva-000-workspace'), profile: 'eva-000',
  } }] }]) + '\n';
  await writeFile(oldPatch, oldContent);
  const previousConfigId = createHash('sha256').update(JSON.stringify({ workspace: paths.workspace, profile: 'eva-000' })).digest('hex').slice(0, 16);
  const previousPatch = join(paths.state, `startup-${installationId}-${previousConfigId}.patch.yml`);
  const previousContent = JSON.stringify([{ insert: [{ id: 'eva-child', config: { workspace: paths.workspace, profile: 'eva-000' } }] }]) + '\n';
  await writeFile(previousPatch, previousContent);
  const creatorConfigId = createHash('sha256').update(JSON.stringify({ workspace: paths.workspace, profile: 'eva-000', defaultAgentPreset: 'cordis', providerDefaultsVersion: 1, dmProtocolVersion: 1 })).digest('hex').slice(0, 16);
  const creatorPatch = join(paths.state, `startup-${installationId}-${creatorConfigId}.patch.yml`);
  const creatorContent = JSON.stringify([{ id: 'agent-preset-registry', config: { default: 'cordis' } }]) + '\n';
  await writeFile(creatorPatch, creatorContent);
  const result = await manager.start(base, 'eva-000');
  assert.equal(result.workspace, paths.workspace);
  assert.equal(await readFile(oldPatch, 'utf8'), oldContent);
  assert.equal(await readFile(previousPatch, 'utf8'), previousContent);
  const patches = (await readdir(paths.state)).filter((name) => name.endsWith('.patch.yml'));
  assert.equal(patches.length, 4);
  assert.equal(await readFile(creatorPatch, 'utf8'), creatorContent);
  const preset = await readEvaPreset();
  const workspaceId = createHash('sha256').update(JSON.stringify({ workspace: paths.workspace, profile: 'eva-000', defaultAgentPreset: 'bot-mode', providerDefaultsVersion: 1, dmProtocolVersion: 1, childHostVersion: 8, dmEntry: resolveDmEntry(), memoryEntry: resolveMemoryEntry(), botModeEntry: resolveBotModeEntry(), subconsciousEntry: resolveSubconsciousEntry(), historyEntry: resolveHistoryEntry(), evaPresetHash: preset.digest, desktopExclusions: [] })).digest('hex').slice(0, 16);
  assert.ok(patches.includes(`startup-${installationId}-${workspaceId}.patch.yml`));
  const overlay = JSON.parse(await readFile(join(paths.state, `startup-${installationId}-${workspaceId}.patch.yml`), 'utf8'));
  assert.deepEqual(overlay[1], { id: 'agent-preset-registry', name: '@deepseek-ai/dsh-agent-preset-registry', config: { default: 'bot-mode' } });
  assert.ok(!JSON.stringify(overlay).includes('selectedDefault'));
});

test('EVA preset cache is immutable, outside the initialized profile, and reused across restart', async t => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  await mkdir(paths.profileDir, { recursive: true });
  const manifestPath = join(paths.profileDir, 'package.json');
  const manifest = JSON.stringify({ private: true, dsh: { profile: { bundles: ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app'] } } }, null, 2) + '\n';
  const userPatch = join(paths.profileDir, 'user-owned.patch.yml');
  await writeFile(manifestPath, manifest);
  await writeFile(userPatch, 'saved-user-default: standard\n');
  const preset = await readEvaPreset();
  const cache = join(paths.home, 'profiles', '.eva-assets', paths.profile);
  const presetPath = join(cache, `preset-eva-${preset.digest}.yml`);
  assert.ok(!presetPath.startsWith(paths.profileDir + '/'), 'official profile initialization may recreate only the actual profile directory');
  await manager.start(base, 'eva-000');
  assert.equal(await readFile(presetPath, 'utf8'), preset.text);
  assert.equal((await lstat(presetPath)).mode & 0o777, 0o600);
  assert.deepEqual(await readdir(cache), [`preset-eva-${preset.digest}.yml`]);
  await assert.rejects(access(join(cache, 'package.json')), { code: 'ENOENT' });
  const patchNames = (await readdir(paths.state)).filter(name => name.endsWith('.patch.yml'));
  const patchText = await readFile(join(paths.state, patchNames[0]), 'utf8');
  await manager.stop(base, 'eva-000');
  await manager.start(base, 'eva-000');
  assert.equal(await readFile(presetPath, 'utf8'), preset.text);
  assert.deepEqual((await readdir(paths.state)).filter(name => name.endsWith('.patch.yml')), patchNames);
  assert.equal(await readFile(join(paths.state, patchNames[0]), 'utf8'), patchText);
  assert.equal(await readFile(manifestPath, 'utf8'), manifest);
  assert.equal(await readFile(userPatch, 'utf8'), 'saved-user-default: standard\n');
});

for (const kind of ['modified file', 'symlink', 'directory']) {
  test(`an existing ${kind} cannot replace the content-addressed EVA preset`, async t => {
    const { base, manager } = await fixture(t);
    const paths = manager.paths('eva-000');
    const preset = await readEvaPreset();
    const cache = join(paths.home, 'profiles', '.eva-assets', paths.profile);
    const presetPath = join(cache, `preset-eva-${preset.digest}.yml`);
    await mkdir(cache, { recursive: true });
    const external = join(base, 'unrelated-preset.yml');
    await writeFile(external, preset.text);
    if (kind === 'symlink') await symlink(external, presetPath);
    else if (kind === 'directory') await mkdir(presetPath);
    else await writeFile(presetPath, 'user-owned modified preset\n');
    await assert.rejects(manager.start(base, 'eva-000'), /refuses to overwrite|existing or modified/);
    await assert.rejects(access(paths.lock), { code: 'ENOENT' });
    assert.equal(manager.nodes.size, 0, 'unsafe artifacts fail before spawning even the fake CLI');
    assert.equal(await readFile(external, 'utf8'), preset.text);
    const stat = await lstat(presetPath);
    if (kind === 'symlink') assert.ok(stat.isSymbolicLink());
    else if (kind === 'directory') assert.ok(stat.isDirectory());
    else assert.equal(await readFile(presetPath, 'utf8'), 'user-owned modified preset\n');
  });
}

test('preset tampering after a successful stop blocks restart rather than silently regenerating it', async t => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  const preset = await readEvaPreset();
  const presetPath = join(paths.home, 'profiles', '.eva-assets', paths.profile, `preset-eva-${preset.digest}.yml`);
  await manager.start(base, 'eva-000');
  await manager.stop(base, 'eva-000');
  await writeFile(presetPath, 'tampered after stop\n');
  await assert.rejects(manager.start(base, 'eva-000'), /existing or modified/);
  await assert.rejects(access(paths.lock), { code: 'ENOENT' });
  assert.equal(await readFile(presetPath, 'utf8'), 'tampered after stop\n');
});

test('preset cache refuses symlinked ancestor directories without touching their target', async t => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  await mkdir(join(paths.home, 'profiles'), { recursive: true });
  const external = join(base, 'unrelated-assets');
  await mkdir(external);
  await symlink(external, join(paths.home, 'profiles', '.eva-assets'), 'dir');
  await assert.rejects(manager.start(base, 'eva-000'), /owned directory|symlink/);
  await assert.rejects(access(paths.lock), { code: 'ENOENT' });
  assert.deepEqual(await readdir(external), []);
});

test('desktop role selections select a new immutable overlay without rewriting existing profiles', async t => {
  const credentialsPath = '/synthetic/shared-credentials.yaml';
  const { base, manager } = await fixture(t, { credentialsPath });
  const paths = manager.paths('eva-000');
  await mkdir(paths.profileDir, { recursive: true });
  const manifestPath = join(paths.profileDir, 'package.json');
  await writeFile(manifestPath, JSON.stringify({ dsh: { profile: { bundles: ['@deepseek-ai/dsh-web-app'] } } }));
  await manager.start(base, 'eva-000');
  await manager.stop(base, 'eva-000');
  const [previousName] = (await readdir(paths.state)).filter(name => name.endsWith('.patch.yml'));
  const previousText = await readFile(join(paths.state, previousName), 'utf8');
  const manifest = JSON.stringify({ private: true, dsh: { profile: { bundles: ['@local/unknown', '@local/eva', '@local/magi-shell'] } } }, null, 2) + '\n';
  await writeFile(manifestPath, manifest);
  await manager.start(base, 'eva-000');
  const desktopExclusions = [
    { id: 'magi-shell', name: '@local/magi-shell', disabled: true },
    { id: 'eva', name: '@local/eva', disabled: true },
  ];
  const preset = await readEvaPreset();
  const installationId = createHash('sha256').update(dirname(fileURLToPath(new URL('../runtime.js', import.meta.url)))).digest('hex').slice(0, 16);
  const configId = createHash('sha256').update(JSON.stringify({ workspace: paths.workspace, profile: paths.profile,
    defaultAgentPreset: 'bot-mode', providerDefaultsVersion: 1, dmProtocolVersion: 1, childHostVersion: 8, dmEntry: resolveDmEntry(), memoryEntry: resolveMemoryEntry(), botModeEntry: resolveBotModeEntry(), subconsciousEntry: resolveSubconsciousEntry(), historyEntry: resolveHistoryEntry(),
    evaPresetHash: preset.digest, desktopExclusions, credentialsPath, credentialSharingVersion: 1 })).digest('hex').slice(0, 16);
  const nextName = `startup-${installationId}-${configId}.patch.yml`;
  assert.notEqual(nextName, previousName);
  assert.deepEqual((await readdir(paths.state)).filter(name => name.endsWith('.patch.yml')).sort(), [previousName, nextName].sort());
  const overlay = JSON.parse(await readFile(join(paths.state, nextName), 'utf8'));
  assert.deepEqual(overlay[4], { id: 'magi-dm', disabled: true });
  assert.deepEqual(overlay[0].insert[1], { id: 'magi-dm-child', name: resolveDmEntry(), config: {} });
  assert.equal(overlay[0].insert.filter(row => row.id === 'magi-dm-child').length, 1, 'one active installed DM root, independent of selected EVA version');
  assert.deepEqual(overlay[5], { id: 'magi-memory', disabled: true });
  assert.deepEqual(overlay[6], { id: 'magi-bot-mode', disabled: true });
  assert.deepEqual(overlay[7], { id: 'magi-bot-mode-preset', disabled: true });
  assert.deepEqual(overlay.slice(9), [...desktopExclusions, { id: 'magi-history-search', disabled: true }]);
  assert.deepEqual(overlay[0].insert[2], { id: 'magi-memory-child', name: resolveMemoryEntry(), config: {} });
  assert.equal(overlay[3].id, 'credentials');
  assert.ok(!JSON.stringify(overlay).includes('@local/unknown'), 'unrelated bundles are not patched or disabled');
  assert.equal(await readFile(manifestPath, 'utf8'), manifest);
  assert.equal(await readFile(join(paths.state, previousName), 'utf8'), previousText);
});

test('malformed existing profile fails before spawning and is not overwritten', async t => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  await mkdir(paths.profileDir, { recursive: true });
  const manifest = join(paths.profileDir, 'package.json');
  await writeFile(manifest, '{ do not repair user-owned content');
  await assert.rejects(manager.start(base, 'eva-000'), SyntaxError);
  assert.equal(await readFile(manifest, 'utf8'), '{ do not repair user-owned content');
  await assert.rejects(access(paths.lock), { code: 'ENOENT' });
  assert.equal(manager.nodes.size, 0);
});

test('root workspace start preserves registered legacy workspace without migration', async (t) => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  const legacy = join(base, 'Documents/deepseek-harness/eva-000-workspace');
  await mkdir(legacy, { recursive: true });
  await writeFile(join(legacy, 'note.txt'), 'preserved');
  await mkdir(join(paths.home, 'storages'), { recursive: true });
  const id = '11111111-1111-4111-8111-111111111111';
  const registry = { unit: { name: 'workspace', version: 2 }, global: { workspaceIds: [id] },
    tables: { workspaces: { [id]: { path: legacy, title: 'Preserved title', sessionIds: [], archivedSessionIds: [] } } } };
  const registryPath = join(paths.home, 'storages/workspace.json');
  await writeFile(registryPath, JSON.stringify(registry));
  const result = await manager.start(base, 'eva-000');
  assert.equal(result.workspace, paths.workspace);
  assert.equal(result.workspace, paths.home);
  assert.equal(await readFile(join(legacy, 'note.txt'), 'utf8'), 'preserved');
  assert.equal(await readFile(registryPath, 'utf8'), JSON.stringify(registry));
  await assert.rejects(access(join(paths.home, 'note.txt')), { code: 'ENOENT' });
  await assert.rejects(access(join(paths.state, 'workspace-relocation.json')), { code: 'ENOENT' });
});

test('start preserves historical nested workspace without rewriting history', async (t) => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  const legacy = join(paths.home, 'workspace');
  await mkdir(legacy, { recursive: true });
  await writeFile(join(legacy, 'note.txt'), 'untouched');
  await mkdir(join(paths.home, 'storages'), { recursive: true });
  const id = '11111111-1111-4111-8111-111111111111';
  const registry = { unit: { name: 'workspace', version: 2 }, global: { workspaceIds: [id] },
    tables: { workspaces: { [id]: { path: legacy, sessionIds: ['history'] } } } };
  const registryPath = join(paths.home, 'storages/workspace.json');
  const original = JSON.stringify(registry);
  await writeFile(registryPath, original);
  assert.equal((await manager.start(base, 'eva-000')).workspace, paths.home);
  assert.equal(await readFile(registryPath, 'utf8'), original);
  assert.equal(await readFile(join(legacy, 'note.txt'), 'utf8'), 'untouched');
  await assert.rejects(access(join(paths.home, 'note.txt')), { code: 'ENOENT' });
  await manager.stop(base, 'eva-000');
  assert.equal((await manager.start(base, 'eva-000')).workspace, paths.home);
  assert.equal(await readFile(registryPath, 'utf8'), original);
});

test('root plus other registered workspaces survive restart without nested creation', async (t) => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  await mkdir(join(paths.home, 'storages'), { recursive: true });
  const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'];
  const registry = { unit: { name: 'workspace', version: 2 }, global: { workspaceIds: ids },
    tables: { workspaces: { [ids[0]]: { path: paths.home, sessionIds: ['main'] },
      [ids[1]]: { path: join(base, 'other'), sessionIds: ['ordinary'] } } } };
  const registryPath = join(paths.home, 'storages/workspace.json');
  const original = JSON.stringify(registry, null, 2) + '\n';
  await writeFile(registryPath, original);
  assert.equal((await manager.start(base, 'eva-000')).workspace, paths.home);
  await manager.stop(base, 'eva-000');
  assert.equal((await manager.start(base, 'eva-000')).workspace, paths.home);
  assert.equal(await readFile(registryPath, 'utf8'), original);
  await assert.rejects(access(join(paths.home, 'workspace')), { code: 'ENOENT' });
});

test('startup timeout kills the child and releases ownership', async (t) => {
  const { base, manager } = await fixture(t, { startupTimeout: 200 });
  await assert.rejects(manager.start(base, 'timeout'), /timed out/);
  assert.equal((await manager.status(base, 'timeout')).status, 'stopped');
  await assert.rejects(access(join(base, '.dsh-timeout/eva/owner.lock')));
});

test('early process failure is reported, not readiness', async (t) => {
  const { base, manager } = await fixture(t);
  await assert.rejects(manager.start(base, 'failure'), /before readiness/);
  assert.equal((await manager.status(base, 'failure')).status, 'failed');
});

test('cancelling startup does not leave a process or lock', async (t) => {
  const { base, manager } = await fixture(t);
  const abort = new AbortController();
  const pending = manager.start(base, 'slow', abort.signal);
  const timer = setTimeout(() => abort.abort(new Error('test cancelled')), 50);
  try { await assert.rejects(pending, /test cancelled/); } finally { clearTimeout(timer); }
  await assert.rejects(access(join(base, '.dsh-slow/eva/owner.lock')));
});

test('unloading during startup terminates the child', async (t) => {
  const { base, manager } = await fixture(t);
  const pending = manager.start(base, 'slow');
  // Start has an asynchronous filesystem setup; disposal must handle both sides of spawn.
  const timer = setTimeout(() => { void manager.dispose(); }, 40);
  try { await pending.catch(() => {}); await manager.dispose(); } finally { clearTimeout(timer); }
  assert.notEqual((await manager.status(base, 'slow')).status, 'running');
  await assert.rejects(access(join(base, '.dsh-slow/eva/owner.lock')));
});

test('pre-existing home symlinks are refused', async (t) => {
  const { base, manager } = await fixture(t);
  await mkdir(join(base, 'elsewhere'));
  await symlink(join(base, 'elsewhere'), join(base, '.dsh-eva-000'));
  await assert.rejects(manager.start(base, 'eva-000'), /symlink/i);
});

test('storage symlinks are refused even with root workspace', async (t) => {
  const { base, manager } = await fixture(t);
  await mkdir(join(base, 'elsewhere'));
  await mkdir(join(base, '.dsh-eva-000'));
  await symlink(join(base, 'elsewhere'), join(base, '.dsh-eva-000/storages'));
  await assert.rejects(manager.start(base, 'eva-000'), /symlink/i);
  await assert.rejects(access(join(base, '.dsh-eva-000/eva/owner.lock')));
});

test('profile descendants cannot redirect to another profile', async (t) => {
  const { base, manager } = await fixture(t);
  const profiles = join(base, '.dsh-eva-000/profiles');
  await mkdir(profiles, { recursive: true });
  await mkdir(join(base, 'outside'));
  await symlink(join(base, 'outside'), join(profiles, 'eva-000'));
  await assert.rejects(manager.start(base, 'eva-000'), /symlink/i);
  await assert.rejects(access(join(base, '.dsh-eva-000/eva/owner.lock')));
});

test('first initialization never erases existing non-profile files', async (t) => {
  const { base, manager } = await fixture(t);
  const { writeFile, readFile } = await import('node:fs/promises');
  const profile = join(base, '.dsh-eva-000/profiles/eva-000');
  await mkdir(profile, { recursive: true });
  await writeFile(join(profile, 'important.txt'), 'keep me');
  await assert.rejects(manager.start(base, 'eva-000'), /nonempty profile/);
  assert.equal(await readFile(join(profile, 'important.txt'), 'utf8'), 'keep me');
});

test('common credential formats are redacted and logs are not tool output', async (t) => {
  for (const value of ['Authorization: Bearer super-secret-token', '{"api_key":"super-secret-token"}']) {
    assert.ok(!redact(value).includes('super-secret-token'));
  }
  const { base, manager } = await fixture(t);
  const result = await manager.start(base, 'eva-000');
  assert.ok(!('diagnostics' in result));
});

async function waitForDeath(pid) {
  const { setTimeout } = await import('node:timers/promises');
  for (let count = 0; count < 40; count++) {
    try { process.kill(pid, 0); }
    catch (error) { if (error.code === 'ESRCH') return; throw error; }
    await setTimeout(25);
  }
  assert.fail(`Process ${pid} survived cleanup.`);
}

test('stop cleans descendants that ignore SIGTERM and hold output pipes', { skip: process.platform === 'win32', timeout: 5000 }, async (t) => {
  const { base, manager } = await fixture(t);
  await manager.start(base, 'tree');
  const leafPid = [...manager.nodes.values()][0].ready.leafPid;
  assert.ok(leafPid);
  await manager.stop(base, 'tree');
  await waitForDeath(leafPid);
});

test('a killed controller does not orphan its Host or descendant', { skip: process.platform === 'win32', timeout: 5000 }, async (t) => {
  const { base } = await fixture(t);
  const { spawn } = await import('node:child_process');
  const { once } = await import('node:events');
  const controller = spawn(process.execPath, [fileURLToPath(new URL('./fake-controller.js', import.meta.url)), base], { stdio: ['ignore', 'pipe', 'pipe', 'ipc'] });
  t.after(() => { if (controller.exitCode === null && controller.signalCode === null) controller.kill('SIGTERM'); });
  const [facts] = await once(controller, 'message');
  controller.kill('SIGKILL');
  await once(controller, 'exit');
  await waitForDeath(facts.pid);
  await waitForDeath(facts.leafPid);
});
