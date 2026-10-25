import test from 'node:test';
import assert from 'node:assert/strict';
import fs, { lstat, mkdir, mkdtemp, readFile, readdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { createServer } from 'node:net';
import { ensureWorkspace } from '../relocate-workspace.js';

const id = '111e2849-79ba-44e9-9c8d-20a11c80f995';
const bytes = Buffer.from([0, 255, 128, 1, 10, 0]);
async function fixture(t, registry = true) {
  const temp = await realpath(tmpdir());
  const root = await mkdtemp(join(temp, 'eva-relocation-'));
  const handle = 'eva-123';
  const home = join(root, `.dsh-${handle}`);
  const state = join(home, 'eva');
  const workspace = join(home, 'workspace');
  const legacyWorkspace = join(root, 'Documents', 'deepseek-harness', `${handle}-workspace`);
  await mkdir(state, { recursive: true });
  const registryPath = join(home, 'storages', 'workspace.json');
  const data = { unit: { name: 'workspace', version: 2 },
    global: { initialized: true, workspaceIds: [id], archivedSessionIds: [], pinnedSessionIds: [], pendingMutation: null },
    tables: { workspaces: { [id]: { path: legacyWorkspace, title: 'Keep exact title', sessionIds: [], createdAt: 'keep', custom: { keep: true } } } } };
  if (registry) {
    await mkdir(dirname(registryPath));
    await writeFile(registryPath, JSON.stringify(data));
    await mkdir(join(legacyWorkspace, 'nested'), { recursive: true });
    await writeFile(join(legacyWorkspace, 'nested', 'bytes.bin'), bytes);
    await writeFile(join(legacyWorkspace, 'text'), 'exact text\n');
    await writeFile(join(dirname(legacyWorkspace), 'parent-sentinel'), 'never remove parent');
  }
  t.after(async () => {
    assert.equal(dirname(root), temp);
    assert.match(basename(root), /^eva-relocation-[A-Za-z0-9]+$/);
    assert.equal(await realpath(root), root);
    assert.ok((await lstat(root)).isDirectory());
    await rm(root, { recursive: true });
  });
  return { root, home, state, workspace, legacyWorkspace, registryPath, data,
    pending: join(state, 'workspace-relocation.pending.json'),
    facts: join(state, 'workspace-relocation.json'), options: { home, handle, legacyWorkspace } };
}
async function missing(path) { await assert.rejects(lstat(path), { code: 'ENOENT' }); }
async function unchangedRefusal(f, pattern) {
  const original = await readFile(f.registryPath);
  await assert.rejects(ensureWorkspace(f.options), pattern);
  assert.deepEqual(await readFile(f.registryPath), original);
  await missing(f.pending);
  await missing(f.workspace);
  assert.deepEqual(await readFile(join(f.legacyWorkspace, 'nested', 'bytes.bin')), bytes);
}
// Patch built-in bindings, not the helper API; restores before fixture teardown.
async function patch(t, method, implementation) {
  const original = fs[method];
  t.mock.method(fs, method, implementation(original));
  syncBuiltinESMExports();
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
}

test('fresh start creates only nested workspace and never probes legacy Documents', async (t) => {
  const f = await fixture(t, false);
  const probes = [];
  for (const method of ['lstat', 'realpath', 'readdir', 'readFile', 'open', 'mkdir']) {
    await patch(t, method, (original) => async (path, ...args) => {
      probes.push(String(path));
      if (String(path).startsWith(join(f.root, 'Documents'))) throw new Error('legacy path was probed');
      return original(path, ...args);
    });
  }
  assert.equal(await ensureWorkspace(f.options), f.workspace);
  assert.ok((await lstat(f.workspace)).isDirectory());
  assert.deepEqual((await readdir(f.home)).sort(), ['eva', 'workspace']);
  assert.equal(probes.some((path) => path.includes('Documents')), false);
});

test('fresh registry absence also works with existing safe storages directory', async (t) => {
  const f = await fixture(t, false);
  await mkdir(join(f.home, 'storages'));
  assert.equal(await ensureWorkspace({ ...f.options, legacyWorkspace: undefined }), f.workspace);
});

test('nested authoritative registry preserves bytes and existing history without mutation', async (t) => {
  const f = await fixture(t, false);
  await mkdir(dirname(f.registryPath));
  f.data.tables.workspaces[id].path = f.workspace;
  f.data.tables.workspaces[id].sessionIds = ['existing-history-is-not-being-migrated'];
  await writeFile(f.registryPath, JSON.stringify(f.data));
  await mkdir(f.workspace);
  await writeFile(join(f.workspace, 'keep'), bytes);
  const before = await readFile(f.registryPath);
  assert.equal(await ensureWorkspace({ ...f.options, legacyWorkspace: undefined }), f.workspace);
  assert.deepEqual(await readFile(f.registryPath), before);
  assert.deepEqual(await readFile(join(f.workspace, 'keep')), bytes);
  await missing(f.pending);
  await missing(f.facts);
});

test('nested registry safely recreates missing directory but refuses a linked target', async (t) => {
  const f = await fixture(t, false);
  await mkdir(dirname(f.registryPath));
  f.data.tables.workspaces[id].path = f.workspace;
  await writeFile(f.registryPath, JSON.stringify(f.data));
  assert.equal(await ensureWorkspace(f.options), f.workspace);
  await rm(f.workspace, { recursive: true });
  await symlink(f.root, f.workspace);
  await assert.rejects(ensureWorkspace(f.options), /symlink/);
});

test('successful relocation preserves all bytes and UUID, updates only path, removes only source', async (t) => {
  const f = await fixture(t);
  assert.equal(await ensureWorkspace(f.options), f.workspace);
  assert.deepEqual(await readFile(join(f.workspace, 'nested', 'bytes.bin')), bytes);
  assert.equal(await readFile(join(f.workspace, 'text'), 'utf8'), 'exact text\n');
  const expected = structuredClone(f.data);
  expected.tables.workspaces[id].path = f.workspace;
  assert.deepEqual(JSON.parse(await readFile(f.registryPath, 'utf8')), expected);
  await missing(f.legacyWorkspace);
  assert.equal(await readFile(join(dirname(f.legacyWorkspace), 'parent-sentinel'), 'utf8'), 'never remove parent');
  await missing(f.pending);
  const facts = JSON.parse(await readFile(f.facts, 'utf8'));
  assert.equal(facts.workspaceId, id);
  assert.equal(facts.backupRetained, false);
  assert.ok(!JSON.stringify(facts).includes('sha256'));
  assert.equal(await ensureWorkspace(f.options), f.workspace);
});

for (const key of ['sessionIds', 'archivedSessionIds', 'pinnedSessionIds', 'pendingMutation']) {
  test(`${key} refuses migration before any mutation`, async (t) => {
    const f = await fixture(t);
    f.data.global[key] = key === 'pendingMutation' ? { kind: 'pending' } : ['history'];
    await writeFile(f.registryPath, JSON.stringify(f.data));
    await unchangedRefusal(f, /historical sessions|pendingMutation/);
  });
}

for (const kind of ['wrong unit', 'wrong version', 'extra record', 'extra id', 'extra table', 'mismatched id', 'wrong path', 'relative path']) {
  test(`unexpected registry ${kind} is refused without mutations`, async (t) => {
    const f = await fixture(t);
    if (kind === 'wrong unit') f.data.unit.name = 'other';
    if (kind === 'wrong version') f.data.unit.version = 3;
    if (kind === 'extra record') f.data.tables.workspaces.other = { path: f.legacyWorkspace };
    if (kind === 'extra id') f.data.global.workspaceIds.push(id);
    if (kind === 'extra table') f.data.tables.sessions = {};
    if (kind === 'mismatched id') f.data.global.workspaceIds[0] = '222e2849-79ba-44e9-9c8d-20a11c80f995';
    if (kind === 'wrong path') f.data.tables.workspaces[id].path = f.root;
    if (kind === 'relative path') f.data.tables.workspaces[id].path = 'relative';
    await writeFile(f.registryPath, JSON.stringify(f.data));
    await unchangedRefusal(f, /registry|workspace|path|canonical/);
  });
}

test('existing legacy target refuses even when empty and never overwrites', async (t) => {
  const f = await fixture(t);
  await mkdir(f.workspace);
  const before = await readFile(f.registryPath);
  await assert.rejects(ensureWorkspace(f.options), /already exists/);
  assert.deepEqual(await readFile(f.registryPath), before);
  assert.deepEqual(await readdir(f.workspace), []);
  await missing(f.pending);
});

test('missing migration source refuses before creating target or marker', async (t) => {
  const f = await fixture(t);
  assert.equal(basename(f.legacyWorkspace), `${f.options.handle}-workspace`);
  await rm(f.legacyWorkspace, { recursive: true });
  await assert.rejects(ensureWorkspace(f.options), { code: 'ENOENT' });
  await missing(f.workspace);
  await missing(f.pending);
});

for (const kind of ['source root', 'source child', 'target', 'registry', 'storages', 'home alias']) {
  test(`refuses symlink at ${kind}`, async (t) => {
    const f = await fixture(t);
    if (kind === 'source root') {
      await rm(f.legacyWorkspace, { recursive: true });
      await symlink(f.root, f.legacyWorkspace);
    }
    if (kind === 'source child') await symlink(f.root, join(f.legacyWorkspace, 'linked'));
    if (kind === 'target') await symlink(f.root, f.workspace);
    if (kind === 'registry') {
      await rm(f.registryPath);
      await symlink(join(f.root, 'elsewhere'), f.registryPath);
    }
    if (kind === 'storages') {
      await fs.rename(dirname(f.registryPath), join(f.root, 'storage-actual'));
      await symlink(join(f.root, 'storage-actual'), dirname(f.registryPath));
    }
    if (kind === 'home alias') {
      const alias = join(f.root, 'alias');
      await symlink(f.home, alias);
      f.options.home = alias;
    }
    await assert.rejects(ensureWorkspace(f.options), /symlink|already exists/);
    await missing(f.pending);
  });
}

test('source sockets refuse without copy or mutations', async (t) => {
  const f = await fixture(t);
  const server = createServer();
  // Bind at a short pathname (macOS sockaddr_un has a small path limit),
  // then move the socket inode into the source tree before inspection.
  const socketPath = join(f.root, 'socket');
  await new Promise((done, fail) => { server.once('error', fail); server.listen(socketPath, done); });
  await fs.rename(socketPath, join(f.legacyWorkspace, 'socket'));
  try { await unchangedRefusal(f, /regular files/); }
  finally { await new Promise((done) => server.close(done)); }
});

test('pending marker blocks boot even with no registry or with a nested registry', async (t) => {
  const f = await fixture(t, false);
  await writeFile(f.pending, 'untrusted metadata is never parsed');
  await assert.rejects(ensureWorkspace(f.options), /incomplete workspace relocation/);
  await missing(f.workspace);
  await mkdir(dirname(f.registryPath));
  f.data.tables.workspaces[id].path = f.workspace;
  await writeFile(f.registryPath, JSON.stringify(f.data));
  await assert.rejects(ensureWorkspace(f.options), /incomplete workspace relocation/);
  await missing(f.workspace);
});

test('copy failure retains original, partial target and marker; retry refuses', async (t) => {
  const f = await fixture(t);
  await patch(t, 'cp', (original) => async (...args) => {
    await original(...args);
    throw new Error('injected copy failure');
  });
  await assert.rejects(ensureWorkspace(f.options), /injected copy failure/);
  assert.deepEqual(await readFile(join(f.legacyWorkspace, 'nested', 'bytes.bin')), bytes);
  assert.ok((await lstat(f.workspace)).isDirectory());
  assert.ok((await lstat(f.pending)).isFile());
  assert.deepEqual(JSON.parse(await readFile(f.registryPath, 'utf8')), f.data);
  await assert.rejects(ensureWorkspace(f.options), /incomplete workspace relocation/);
});

test('concurrent source edit after copy is never deleted', async (t) => {
  const f = await fixture(t);
  await patch(t, 'cp', (original) => async (...args) => {
    await original(...args);
    await writeFile(join(f.legacyWorkspace, 'text'), 'concurrent edit');
  });
  await assert.rejects(ensureWorkspace(f.options), /changed/);
  assert.equal(await readFile(join(f.legacyWorkspace, 'text'), 'utf8'), 'concurrent edit');
  assert.ok((await lstat(f.pending)).isFile());
});

test('registry mutation during copy is not overwritten and original remains', async (t) => {
  const f = await fixture(t);
  await patch(t, 'cp', (original) => async (...args) => {
    await original(...args);
    await writeFile(f.registryPath, '{"concurrently":"changed"}');
  });
  await assert.rejects(ensureWorkspace(f.options), /registry changed/);
  assert.equal(await readFile(f.registryPath, 'utf8'), '{"concurrently":"changed"}');
  assert.deepEqual(await readFile(join(f.legacyWorkspace, 'nested', 'bytes.bin')), bytes);
  assert.ok((await lstat(f.pending)).isFile());
});

test('empty initialized registry acts fresh without legacy probes or registry mutation', async (t) => {
  const f = await fixture(t, false);
  await mkdir(dirname(f.registryPath));
  f.data.global.workspaceIds = [];
  f.data.tables.workspaces = {};
  await writeFile(f.registryPath, JSON.stringify(f.data));
  const before = await readFile(f.registryPath);
  await patch(t, 'lstat', (original) => async (path, ...args) => {
    assert.ok(!String(path).includes('Documents'), 'fresh start must not probe Documents');
    return original(path, ...args);
  });
  assert.equal(await ensureWorkspace({ ...f.options, legacyWorkspace: undefined }), f.workspace);
  assert.deepEqual(await readFile(f.registryPath), before);
  await missing(f.pending);
});

test('nested registered workspace allows other records and history without mutating registry', async (t) => {
  const f = await fixture(t, false);
  await mkdir(dirname(f.registryPath));
  const otherId = '222e2849-79ba-44e9-9c8d-20a11c80f995';
  f.data.tables.workspaces[id].path = f.workspace;
  f.data.tables.workspaces[id].sessionIds = ['existing'];
  f.data.global.workspaceIds.push(otherId);
  f.data.global.archivedSessionIds = ['history'];
  f.data.tables.workspaces[otherId] = { path: join(f.root, 'other'), sessionIds: ['other-history'] };
  await writeFile(f.registryPath, JSON.stringify(f.data));
  const before = await readFile(f.registryPath);
  assert.equal(await ensureWorkspace({ ...f.options, legacyWorkspace: undefined }), f.workspace);
  assert.deepEqual(await readFile(f.registryPath), before);
  await missing(f.pending);
});

test('duplicate registered nested paths refuse without mutation', async (t) => {
  const f = await fixture(t, false);
  await mkdir(dirname(f.registryPath));
  const otherId = '222e2849-79ba-44e9-9c8d-20a11c80f995';
  f.data.tables.workspaces[id].path = f.workspace;
  f.data.global.workspaceIds.push(otherId);
  f.data.tables.workspaces[otherId] = { path: f.workspace, sessionIds: [] };
  await writeFile(f.registryPath, JSON.stringify(f.data));
  await assert.rejects(ensureWorkspace(f.options), /duplicate nested/);
  await missing(f.pending);
  await missing(f.workspace);
});

test('silent cleanup no-op cannot report success or clear pending marker', async (t) => {
  const f = await fixture(t);
  await patch(t, 'rm', (original) => async (path, ...args) => {
    if (path !== f.legacyWorkspace) return original(path, ...args);
  });
  await assert.rejects(ensureWorkspace(f.options), /not removed/);
  assert.deepEqual(await readFile(join(f.legacyWorkspace, 'nested', 'bytes.bin')), bytes);
  assert.ok((await lstat(f.pending)).isFile());
});

test('source cleanup failure cannot report success and leaves marker blocking committed registry', async (t) => {
  const f = await fixture(t);
  await patch(t, 'rm', (original) => async (path, ...args) => {
    if (path === f.legacyWorkspace) throw new Error('injected cleanup failure');
    return original(path, ...args);
  });
  await assert.rejects(ensureWorkspace(f.options), /injected cleanup failure/);
  assert.deepEqual(await readFile(join(f.legacyWorkspace, 'nested', 'bytes.bin')), bytes);
  assert.ok((await lstat(f.pending)).isFile());
  assert.equal(JSON.parse(await readFile(f.registryPath, 'utf8')).tables.workspaces[id].path, f.workspace);
  await assert.rejects(ensureWorkspace(f.options), /incomplete workspace relocation/);
});
