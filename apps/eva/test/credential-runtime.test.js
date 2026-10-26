import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, realpath, mkdir, readFile, readdir, writeFile, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { EvaRuntime } from '../runtime.js';

const runtime = { executable: process.execPath, cli: fileURLToPath(new URL('./fake-cli.js', import.meta.url)),
  runtimeDir: '/unused', supportDir: '/unused' };
async function fixture(t, credentialsPath) {
  const root = await realpath(tmpdir());
  const base = await mkdtemp(join(root, 'eva-credential-runtime-'));
  const manager = new EvaRuntime(runtime, { homeRoot: base, startupTimeout: 3000, stopTimeout: 1000, credentialsPath });
  t.after(async () => {
    await manager.dispose();
    assert.ok(base.startsWith(join(root, 'eva-credential-runtime-')));
    await rm(base, { recursive: true });
  });
  return { base, manager };
}
async function patches(state) {
  return Promise.all((await readdir(state)).filter((name) => name.endsWith('.patch.yml')).map(async (name) => {
    const text = await readFile(join(state, name), 'utf8');
    return { name, text, data: JSON.parse(text) };
  }));
}

test('shared startup points official provider at one parent store and preserves child credentials/profile/workspace', async (t) => {
  const { base, manager } = await fixture(t, '/absent-parent-fixture/credential store.yaml');
  const paths = manager.paths('eva-000');
  await mkdir(paths.home);
  const dormant = join(paths.home, '.credentials.yaml');
  await writeFile(dormant, 'synthetic child credentials, do not merge\n');
  await writeFile(join(paths.home, 'work.txt'), 'preserve workspace');
  const first = await manager.start();
  // Unnamed allocation preserves occupied homes; wake the original explicitly.
  assert.equal(first.handle, 'eva-001');
  const node = await manager.start(undefined, 'eva-000');
  assert.equal(node.home, node.workspace);
  assert.equal(node.home, node.cwd);
  assert.ok(!JSON.stringify(node).includes('/absent-parent-fixture'));
  assert.match(node.credentials, /share/);
  const [patch] = await patches(paths.state);
  assert.deepEqual(patch.data.find((row) => row.id === 'credentials'), { id: 'credentials', name: '@deepseek-ai/dsh-credentials-local', disabled: false,
    config: { path: '/absent-parent-fixture/credential store.yaml', watch: true } });
  assert.equal(patch.data[0].insert[0].config.credentialsPath, patch.data.find((row) => row.id === 'credentials').config.path);
  assert.equal(patch.data[0].insert[0].config.credentialSharingVersion, 1);
  assert.equal(await readFile(dormant, 'utf8'), 'synthetic child credentials, do not merge\n');
  assert.equal(await readFile(join(paths.home, 'work.txt'), 'utf8'), 'preserve workspace');
  await manager.stop(undefined, 'eva-000');
  await manager.start(undefined, 'eva-000');
  assert.deepEqual(await patches(paths.state), [patch], 'restart reuses immutable overlay');
});

test('switching stores and upgrading isolated nodes use new overlays without rewriting old files', async (t) => {
  const { base, manager: seed } = await fixture(t);
  await seed.start(undefined, 'eva-000');
  await seed.dispose();
  const state = seed.paths('eva-000').state;
  const original = await patches(state);
  for (const path of ['/synthetic/parent-a.yaml', '/synthetic/parent-b.yaml']) {
    const manager = new EvaRuntime(runtime, { homeRoot: base, credentialsPath: path });
    try { await manager.start(undefined, 'eva-000'); } finally { await manager.dispose(); }
  }
  const all = await patches(state);
  assert.equal(all.length, 3);
  assert.equal(all.find((patch) => patch.name === original[0].name).text, original[0].text);
  assert.deepEqual(new Set(all.slice().map((patch) => patch.data.find((row) => row.id === 'credentials')?.config.path)), new Set([undefined, '/synthetic/parent-a.yaml', '/synthetic/parent-b.yaml']));
});

for (const credentialsPath of ['relative.yaml', '', async () => { throw new Error('unsupported provider'); }]) {
  test('unsupported sharing configuration fails before allocating an unnamed home', async (t) => {
    const { base, manager } = await fixture(t, credentialsPath);
    await assert.rejects(manager.start());
    assert.deepEqual(await readdir(base), []);
  });
}

test('sharing requires affirmative child verification and releases failed-start lock', async (t) => {
  const { manager } = await fixture(t, '/synthetic/parent.yaml');
  await assert.rejects(manager.start(undefined, 'missing-sharing'), /did not verify/);
  await assert.rejects(access(manager.paths('missing-sharing').lock), { code: 'ENOENT' });
});
