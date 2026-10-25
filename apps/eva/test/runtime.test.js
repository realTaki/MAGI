import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, rmdir, mkdir, symlink, access, realpath, lstat, readdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { EvaRuntime, validateName, redact } from '../runtime.js';

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
  assert.equal(a.workspace, join(base, '.dsh-eva-000/workspace'));
  assert.equal(a.home, join(base, '.dsh-eva-000'));
  assert.equal(a.cwd, a.home);
  assert.notEqual(a.cwd, a.workspace);
  assert.equal(a.defaultAgentPreset, 'cordis');
  assert.equal((await manager.status(base, 'eva-000')).pid, a.pid);
  const other = new EvaRuntime(runtime, { homeRoot: base });
  t.after(() => other.dispose());
  await assert.rejects(other.start(base, 'eva-000'), /locked/);
  assert.equal((await manager.stop(base, 'eva-000')).status, 'stopped');
  await assert.rejects(access(join(base, '.dsh-eva-000/eva/owner.lock')));
  const next = await manager.start(base, 'eva-000');
  assert.notEqual(next.pid, a.pid);
  assert.equal((await manager.stop(base, 'eva-000')).status, 'stopped');
});

for (const handle of ['wrong-cwd', 'missing-cwd', 'missing-preset']) {
  test(`readiness refuses missing or inconsistent isolation facts: ${handle}`, async (t) => {
    const { base, manager } = await fixture(t);
    await assert.rejects(manager.start(base, handle), /readiness facts/);
    await assert.rejects(access(manager.paths(handle).lock), { code: 'ENOENT' });
  });
}

test('readiness reports the saved user default instead of forcing Creator', async (t) => {
  const { base, manager } = await fixture(t);
  const node = await manager.start(base, 'user-standard');
  assert.equal(node.defaultAgentPreset, 'standard');
  assert.equal(node.cwd, node.home);
});

test('paths synchronously describe handle-global home, workspace, profile, and state', async (t) => {
  const { base, manager } = await fixture(t);
  assert.deepEqual(manager.paths('melchior'), {
    home: join(base, '.dsh-melchior'),
    workspace: join(base, '.dsh-melchior/workspace'),
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

test('allocation reserves only the home root; start creates its nested workspace', async (t) => {
  const { base, manager } = await fixture(t);
  const handle = await manager.allocateHandle();
  const paths = manager.paths(handle);
  assert.ok((await lstat(paths.home)).isDirectory());
  assert.deepEqual(await readdir(paths.home), []);
  await manager.start(base, handle);
  assert.ok((await lstat(paths.workspace)).isDirectory());
});

test('a fresh start does not create or require Documents', async (t) => {
  const { base, manager } = await fixture(t);
  await assert.rejects(access(join(base, 'Documents')), { code: 'ENOENT' });
  const result = await manager.start(base);
  assert.equal(result.workspace, join(result.home, 'workspace'));
  await assert.rejects(access(join(base, 'Documents')), { code: 'ENOENT' });
});

test('concurrent managers atomically allocate distinct automatic handles', async (t) => {
  const { base, manager } = await fixture(t);
  const other = new EvaRuntime(runtime, { homeRoot: base, startupTimeout: 3000, stopTimeout: 1000 });
  try {
    const [first, second] = await Promise.all([manager.start(base), other.start(base)]);
    assert.deepEqual([first.handle, second.handle].sort(), ['eva-000', 'eva-001']);
    assert.notEqual(first.pid, second.pid);
    assert.deepEqual((await manager.status(base)).nodes.map((node) => node.handle), [first.handle]);
    assert.deepEqual((await other.status(base)).nodes.map((node) => node.handle), [second.handle]);
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
  assert.equal(first.workspace, join(base, '.dsh-melchior/workspace'));
  assert.equal(first.profileDir, join(base, '.dsh-melchior/profiles/melchior'));
  assert.equal((await manager.status(caller, 'melchior')).pid, first.pid);
  assert.deepEqual((await manager.status(caller)).nodes.map((node) => node.handle), ['melchior']);
  const other = new EvaRuntime(runtime, { homeRoot: base });
  t.after(() => other.dispose());
  assert.deepEqual(await other.status(caller), { nodes: [] });
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
  assert.equal(result.workspace, join(base, '.dsh-eva-000/workspace'));
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
  assert.deepEqual(await readdir(paths.state), ['workspace-relocation.pending.json']);
  assert.equal(manager.nodes.size, 0);
});

test('Creator startup patch preserves older installation-only and workspace-config patches', async (t) => {
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
  const result = await manager.start(base, 'eva-000');
  assert.equal(result.workspace, paths.workspace);
  assert.equal(await readFile(oldPatch, 'utf8'), oldContent);
  assert.equal(await readFile(previousPatch, 'utf8'), previousContent);
  const patches = (await readdir(paths.state)).filter((name) => name.endsWith('.patch.yml'));
  assert.equal(patches.length, 3);
  const workspaceId = createHash('sha256').update(JSON.stringify({ workspace: paths.workspace, profile: 'eva-000', defaultAgentPreset: 'cordis' })).digest('hex').slice(0, 16);
  assert.ok(patches.includes(`startup-${installationId}-${workspaceId}.patch.yml`));
  const overlay = JSON.parse(await readFile(join(paths.state, `startup-${installationId}-${workspaceId}.patch.yml`), 'utf8'));
  assert.deepEqual(overlay[1], { id: 'agent-preset-registry', name: '@deepseek-ai/dsh-agent-preset-registry', config: { default: 'cordis' } });
  assert.ok(!JSON.stringify(overlay).includes('selectedDefault'));
});

test('start relocates only a registered empty-history legacy workspace before launch', async (t) => {
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
  assert.equal(await readFile(join(paths.workspace, 'note.txt'), 'utf8'), 'preserved');
  await assert.rejects(access(legacy), { code: 'ENOENT' });
  registry.tables.workspaces[id].path = paths.workspace;
  assert.deepEqual(JSON.parse(await readFile(registryPath, 'utf8')), registry);
  assert.ok((await lstat(join(paths.state, 'workspace-relocation.json'))).isFile());
});

test('start refuses historical legacy workspace before creating a patch or launching', async (t) => {
  const { base, manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  const legacy = join(base, 'Documents/deepseek-harness/eva-000-workspace');
  await mkdir(legacy, { recursive: true });
  await writeFile(join(legacy, 'note.txt'), 'untouched');
  await mkdir(join(paths.home, 'storages'), { recursive: true });
  const id = '11111111-1111-4111-8111-111111111111';
  const registry = { unit: { name: 'workspace', version: 2 }, global: { workspaceIds: [id] },
    tables: { workspaces: { [id]: { path: legacy, sessionIds: ['history'] } } } };
  const registryPath = join(paths.home, 'storages/workspace.json');
  const original = JSON.stringify(registry);
  await writeFile(registryPath, original);
  await assert.rejects(manager.start(base, 'eva-000'), /historical sessions/);
  assert.equal(await readFile(registryPath, 'utf8'), original);
  assert.equal(await readFile(join(legacy, 'note.txt'), 'utf8'), 'untouched');
  assert.deepEqual(await readdir(paths.state), []);
  assert.equal(manager.nodes.size, 0);
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

test('workspace symlinks are refused', async (t) => {
  const { base, manager } = await fixture(t);
  await mkdir(join(base, 'elsewhere'));
  await mkdir(join(base, '.dsh-eva-000'));
  await symlink(join(base, 'elsewhere'), join(base, '.dsh-eva-000/workspace'));
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
