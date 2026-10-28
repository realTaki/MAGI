import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { access, lstat, mkdir, mkdtemp, readFile, readdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { migrateLegacy } from '../migrate-layout.js';

const run = promisify(execFile);
const workspaceId = '111e2849-79ba-44e9-9c8d-20a11c80f995';
const binary = Buffer.from([0, 1, 2, 255, 128, 10]);
async function fixture(t) {
  const temp = await realpath(tmpdir());
  const root = await mkdtemp(join(temp, 'eva-migration-'));
  const homeRoot = join(root, 'host-home');
  const workspaceRoot = join(homeRoot, 'Documents', 'deepseek-harness');
  const legacyRoot = join(homeRoot, 'caller', '.eva', 'nodes', 'second');
  const home = join(legacyRoot, 'home');
  const workspace = join(legacyRoot, 'workspace');
  await mkdir(join(home, 'profiles', 'eva-second'), { recursive: true });
  await mkdir(join(home, 'storages'));
  await mkdir(join(workspace, 'nested'), { recursive: true });
  await mkdir(join(homeRoot, '.dsh'));
  await writeFile(join(homeRoot, '.dsh', 'untouched'), 'parent home must not change');
  const manifest = { name: 'eva-second', private: true, dependencies: { example: '1.2.3' }, config: { keep: true } };
  const registry = { unit: { name: 'workspace', version: 2 },
    global: { initialized: true, workspaceIds: [workspaceId], archivedSessionIds: [], pinnedSessionIds: [] },
    tables: { workspaces: { [workspaceId]: { path: workspace, title: 'EVA · eva-second', sessionIds: [], createdAt: '2026-01-01', updatedAt: '2026-01-01' } } } };
  await writeFile(join(home, 'profiles', 'eva-second', 'package.json'), JSON.stringify(manifest));
  await writeFile(join(home, 'profiles', 'eva-second', 'cordis.yml'), 'config: keep-these-exact-bytes\n');
  await writeFile(join(home, 'storages', 'workspace.json'), JSON.stringify(registry));
  await writeFile(join(home, '.credentials.yaml'), 'fake-test-credentials-only\n');
  await writeFile(join(home, 'events.jsonl'), '{"fake":"event bytes remain untouched"}\n');
  await writeFile(join(workspace, 'nested', 'binary.dat'), binary);
  t.after(async () => {
    // Never delete a derived, symlinked, or non-unique temporary root.
    assert.equal(dirname(root), temp);
    assert.match(basename(root), /^eva-migration-[A-Za-z0-9]+$/);
    assert.equal(resolve(root), root);
    assert.equal(await realpath(root), root);
    assert.ok((await lstat(root)).isDirectory());
    await rm(root, { recursive: true });
  });
  return { root, homeRoot, workspaceRoot, legacyRoot, home, workspace, manifest, registry,
    options: { legacyRoot, homeRoot } };
}
async function snapshot(root) {
  const result = {};
  async function visit(path, prefix = '') {
    for (const entry of (await readdir(path)).sort()) {
      const child = join(path, entry);
      const name = prefix ? `${prefix}/${entry}` : entry;
      const stat = await lstat(child);
      if (stat.isDirectory()) { result[`${name}/`] = 'directory'; await visit(child, name); }
      else result[name] = (await readFile(child)).toString('base64');
    }
  }
  await visit(root);
  return result;
}
async function readJson(path) { return JSON.parse(await readFile(path, 'utf8')); }

test('offline migration preserves copied bytes, UUID, backup, and parent .dsh', async (t) => {
  const f = await fixture(t);
  const original = await snapshot(f.legacyRoot);
  const parent = await snapshot(join(f.homeRoot, '.dsh'));
  const result = await migrateLegacy(f.options);
  assert.equal(result.status, 'migrated');
  assert.equal(result.handle, 'eva-000');
  assert.equal(result.workspaceId, workspaceId);
  assert.equal(result.destination.home, join(f.homeRoot, '.dsh-eva-000'));
  assert.equal(result.destination.workspace, join(f.homeRoot, '.dsh-eva-000', 'workspace'));
  const copiedHome = result.destination.home;
  const profile = result.destination.profileDir;
  const registry = await readJson(join(copiedHome, 'storages', 'workspace.json'));
  const expected = structuredClone(f.registry);
  expected.tables.workspaces[workspaceId].path = result.destination.workspace;
  expected.tables.workspaces[workspaceId].title = 'EVA · eva-000';
  assert.deepEqual(registry, expected);
  assert.deepEqual(await readJson(join(profile, 'package.json')), { ...f.manifest, name: 'dsh-profile-eva-000' });
  assert.deepEqual(await readFile(join(result.destination.workspace, 'nested', 'binary.dat')), binary);
  for (const name of ['.credentials.yaml', 'events.jsonl']) {
    assert.deepEqual(await readFile(join(copiedHome, name)), await readFile(join(f.home, name)));
  }
  assert.deepEqual(await readFile(join(profile, 'cordis.yml')), await readFile(join(f.home, 'profiles', 'eva-second', 'cordis.yml')));
  const backup = await snapshot(f.legacyRoot);
  delete backup['owner.lock'];
  assert.deepEqual(backup, original);
  assert.deepEqual(await snapshot(join(f.homeRoot, '.dsh')), parent);
  await assert.rejects(access(join(copiedHome, 'profiles', 'eva-second')), { code: 'ENOENT' });
  await assert.rejects(access(join(copiedHome, 'eva', 'migration.pending.json')), { code: 'ENOENT' });
  const tombstone = await readJson(join(f.legacyRoot, 'owner.lock'));
  assert.equal(tombstone.purpose, 'migration-tombstone');
  assert.equal(tombstone.migration.workspaceId, workspaceId);
  assert.equal((await readJson(join(copiedHome, 'eva', 'migration.json'))).workspaceId, workspaceId);
  await assert.rejects(migrateLegacy(f.options), /locked/);
});

test('workspace is nested under the allocated node root', async (t) => {
  const f = await fixture(t);
  const result = await migrateLegacy({ legacyRoot: f.legacyRoot, homeRoot: f.homeRoot });
  assert.equal(result.destination.workspace, join(f.homeRoot, '.dsh-eva-000', 'workspace'));
  assert.deepEqual(await readJson(join(result.destination.profileDir, 'package.json')), { ...f.manifest, name: 'dsh-profile-eva-000' });
});

test('existing lock is refused without reading or trusting a stored PID', async (t) => {
  const f = await fixture(t);
  const lock = join(f.legacyRoot, 'owner.lock');
  await writeFile(lock, '{invalid-json and definitely-not-a-PID');
  const before = await snapshot(f.legacyRoot);
  await assert.rejects(migrateLegacy(f.options), /locked/);
  assert.deepEqual(await snapshot(f.legacyRoot), before);
  await assert.rejects(access(join(f.homeRoot, '.dsh-eva-000')), { code: 'ENOENT' });
});

test('recursive source symlinks are refused and the owned legacy lock is released', async (t) => {
  const f = await fixture(t);
  await symlink(join(f.homeRoot, '.dsh'), join(f.workspace, 'escape'));
  await assert.rejects(migrateLegacy(f.options), /symlink/);
  await assert.rejects(access(join(f.legacyRoot, 'owner.lock')), { code: 'ENOENT' });
  await assert.rejects(access(join(f.homeRoot, '.dsh-eva-000')), { code: 'ENOENT' });
  assert.equal(await readFile(join(f.homeRoot, '.dsh', 'untouched'), 'utf8'), 'parent home must not change');
});

test('source root aliases through symlinks are refused', async (t) => {
  const f = await fixture(t);
  const alias = join(f.root, 'legacy-alias');
  await symlink(f.legacyRoot, alias);
  await assert.rejects(migrateLegacy({ ...f.options, legacyRoot: alias }), /symlink/);
  await assert.rejects(access(join(f.legacyRoot, 'owner.lock')), { code: 'ENOENT' });
});

for (const history of ['sessionIds', 'archivedSessionIds', 'pinnedSessionIds', 'pendingMutation']) {
  test(`${history} is refused; partial data stays quarantined and source stays unchanged`, async (t) => {
    const f = await fixture(t);
    if (history === 'sessionIds') f.registry.tables.workspaces[workspaceId].sessionIds = ['historical-session'];
    else if (history === 'pendingMutation') f.registry.global.pendingMutation = { kind: 'in-progress' };
    else f.registry.global[history] = ['historical-session'];
    await writeFile(join(f.home, 'storages', 'workspace.json'), JSON.stringify(f.registry));
    const original = await snapshot(f.legacyRoot);
    await assert.rejects(migrateLegacy(f.options), /Historical sessions|pendingMutation/);
    assert.deepEqual(await snapshot(f.legacyRoot), original);
    const targetHome = join(f.homeRoot, '.dsh-eva-000');
    await access(join(targetHome, 'eva', 'migration.pending.json'));
    await assert.rejects(access(join(targetHome, 'eva', 'migration.json')), { code: 'ENOENT' });
    assert.deepEqual(await readFile(join(targetHome, 'workspace', 'nested', 'binary.dat')), binary);
    assert.deepEqual(await readFile(join(targetHome, 'events.jsonl')), await readFile(join(f.home, 'events.jsonl')));
    await assert.rejects(access(join(f.legacyRoot, 'owner.lock')), { code: 'ENOENT' });
  });
}

test('allocator skips occupied node roots without overwriting either', async (t) => {
  const f = await fixture(t);
  await mkdir(join(f.homeRoot, '.dsh-eva-000'));
  await writeFile(join(f.homeRoot, '.dsh-eva-000', 'keep'), 'occupied home');
  await mkdir(join(f.homeRoot, '.dsh-eva-001', 'workspace'), { recursive: true });
  await writeFile(join(f.homeRoot, '.dsh-eva-001', 'workspace', 'keep'), 'occupied workspace');
  const result = await migrateLegacy(f.options);
  assert.equal(result.handle, 'eva-002');
  assert.equal(await readFile(join(f.homeRoot, '.dsh-eva-000', 'keep'), 'utf8'), 'occupied home');
  assert.equal(await readFile(join(f.homeRoot, '.dsh-eva-001', 'workspace', 'keep'), 'utf8'), 'occupied workspace');
});

test('destination aliases and parent .dsh destinations are refused without user-data changes', async (t) => {
  const f = await fixture(t);
  await mkdir(join(f.homeRoot, 'elsewhere'));
  await symlink(join(f.homeRoot, 'elsewhere'), join(f.homeRoot, 'redirect'));
  await assert.rejects(migrateLegacy({ ...f.options, homeRoot: join(f.homeRoot, 'redirect') }), /symlink/);
  await assert.rejects(migrateLegacy({ ...f.options, homeRoot: join(f.homeRoot, '.dsh') }), /Unsafe/);
  await assert.rejects(migrateLegacy({ ...f.options, workspaceRoot: f.workspaceRoot }), /no longer supported/);
  assert.deepEqual(await readdir(join(f.homeRoot, 'elsewhere')), []);
  assert.deepEqual(await readdir(join(f.homeRoot, '.dsh')), ['untouched']);
});

test('CLI accepts explicit roots after -- and emits only JSON facts', async (t) => {
  const f = await fixture(t);
  const script = fileURLToPath(new URL('../migrate-layout.js', import.meta.url));
  const { stdout, stderr } = await run(process.execPath, [script, '--', f.legacyRoot, f.homeRoot]);
  assert.equal(stderr, '');
  assert.equal(stdout.trim().split('\n').length, 1);
  const result = JSON.parse(stdout);
  assert.equal(result.status, 'migrated');
  assert.equal(result.workspaceId, workspaceId);
  assert.ok(!stdout.includes('fake-test-credentials-only'));
});
