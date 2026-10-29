import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { access, mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { EvaRuntime } from '../runtime.js';
import { readEvaPreset, resolveDmEntry, resolveMemoryEntry, resolveBotModeEntry, resolveSubconsciousEntry } from '../child-host.js';

const runtime = { executable: process.execPath, cli: fileURLToPath(new URL('./fake-cli.js', import.meta.url)),
  runtimeDir: '/unused', supportDir: '/unused' };
const hash = (value) => createHash('sha256').update(value).digest('hex').slice(0, 16);
const installationId = hash(dirname(fileURLToPath(new URL('../runtime.js', import.meta.url))));

async function fixture(t, options = {}) {
  const root = await realpath(tmpdir());
  const base = await mkdtemp(join(root, 'eva-codex-runtime-'));
  const manager = new EvaRuntime(runtime, { homeRoot: base, startupTimeout: 3000, stopTimeout: 1000, ...options });
  t.after(async () => {
    await manager.dispose();
    assert.ok(resolve(base).startsWith(join(root, 'eva-codex-runtime-')));
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

for (const credentialsPath of [undefined, '/synthetic-codex-test/shared-credentials.yaml']) {
  test(`native Codex route activates without copying provider secrets (${credentialsPath ? 'shared' : 'isolated'})`, async (t) => {
    const { manager } = await fixture(t, { credentialsPath });
    const node = await manager.start(undefined, 'eva-000');
    const [patch] = await patches(manager.paths('eva-000').state);
    assert.deepEqual(patch.data[2], {
      id: 'llm-pi-ai', name: '@deepseek-ai/dsh-llm-pi-ai', disabled: false,
      config: { providers: { 'openai-codex': {} } },
    });
    assert.equal(patch.data.some((row) => row.id === 'agent-default-model'), false,
      'provider activation must not overwrite the child default model');
    if (credentialsPath !== undefined) {
      assert.deepEqual(patch.data[3], { id: 'credentials', name: '@deepseek-ai/dsh-credentials-local', disabled: false,
        config: { path: credentialsPath, watch: true } });
    }
    assert.equal(node.handle, 'eva-000');
    assert.equal(node.status, 'running');
    await manager.stop(undefined, 'eva-000');
    await manager.start(undefined, 'eva-000');
    assert.deepEqual(await patches(manager.paths('eva-000').state), [patch], 'restart reuses the immutable overlay');
  });
}

test('provider defaults version creates a distinct overlay and preserves the previous immutable patch', async (t) => {
  const { manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  await mkdir(paths.state, { recursive: true });
  const previousConfig = { workspace: paths.home, profile: 'eva-000', defaultAgentPreset: 'cordis' };
  const previousName = `startup-${installationId}-${hash(JSON.stringify(previousConfig))}.patch.yml`;
  const previousText = JSON.stringify([{ syntheticPreviousOverlay: true }], null, 2) + '\n';
  await writeFile(join(paths.state, previousName), previousText);
  await manager.start(undefined, 'eva-000');
  const all = await patches(paths.state);
  assert.equal(all.length, 2);
  assert.equal(all.find(({ name }) => name === previousName).text, previousText);
  const preset = await readEvaPreset();
  const nextName = `startup-${installationId}-${hash(JSON.stringify({ workspace: paths.home, profile: 'eva-000', defaultAgentPreset: 'bot-mode', providerDefaultsVersion: 1, dmProtocolVersion: 1, childHostVersion: 7, dmEntry: resolveDmEntry(), memoryEntry: resolveMemoryEntry(), botModeEntry: resolveBotModeEntry(), subconsciousEntry: resolveSubconsciousEntry(), evaPresetHash: preset.digest, desktopExclusions: [] }))}.patch.yml`;
  assert.notEqual(nextName, previousName);
  assert.ok(all.some(({ name }) => name === nextName), 'provider-default version is part of the immutable startup filename');
});

test('DM protocol version selects a new immutable overlay without replacing the provider-default overlay', async t => {
  const { manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  await mkdir(paths.state, { recursive: true });
  const previousConfig = { workspace: paths.home, profile: 'eva-000', defaultAgentPreset: 'cordis', providerDefaultsVersion: 1 };
  const previousName = `startup-${installationId}-${hash(JSON.stringify(previousConfig))}.patch.yml`;
  const previousText = JSON.stringify([{ previousWithoutDm: true }], null, 2) + '\n';
  await writeFile(join(paths.state, previousName), previousText);
  await manager.start(undefined, 'eva-000');
  const all = await patches(paths.state);
  assert.equal(all.length, 2);
  assert.equal(all.find(patch => patch.name === previousName).text, previousText);
  const preset = await readEvaPreset();
  const nextName = `startup-${installationId}-${hash(JSON.stringify({ workspace: paths.home, profile: 'eva-000', defaultAgentPreset: 'bot-mode', providerDefaultsVersion: 1, dmProtocolVersion: 1, childHostVersion: 7, dmEntry: resolveDmEntry(), memoryEntry: resolveMemoryEntry(), botModeEntry: resolveBotModeEntry(), subconsciousEntry: resolveSubconsciousEntry(), evaPresetHash: preset.digest, desktopExclusions: [] }))}.patch.yml`;
  assert.notEqual(nextName, previousName);
  const next = all.find(patch => patch.name === nextName);
  assert.ok(next, 'DM protocol version is part of the immutable startup filename');
  assert.equal(next.data[0].insert.length, 6);
  assert.deepEqual(next.data[0].insert[2], { id: 'magi-memory-child', name: resolveMemoryEntry(), config: {} });
  assert.deepEqual(next.data[0].insert[1], { id: 'magi-dm-child', name: resolveDmEntry(), config: {} });
  assert.equal(next.data[0].insert[0].id, 'eva-child');
  assert.equal(next.data[0].insert[0].name, fileURLToPath(new URL('../child.js', import.meta.url)));
  assert.deepEqual(next.data[0].insert[0].config, { workspace: paths.home, profile: 'eva-000' });
  assert.equal(next.data[0].insert.some(row => row.id === 'eva-dm-child'), false);
});

for (const previousVersion of [1, 2, 3, 4, 5]) test(`independent child composition preserves immutable child overlay v${previousVersion}`, async t => {
  const { manager } = await fixture(t);
  const paths = manager.paths('eva-000');
  const preset = await readEvaPreset();
  const previousConfig = { workspace: paths.home, profile: 'eva-000', defaultAgentPreset: 'eva',
    providerDefaultsVersion: 1, dmProtocolVersion: 1, childHostVersion: previousVersion, evaPresetHash: preset.digest, desktopExclusions: [] };
  const previousName = `startup-${installationId}-${hash(JSON.stringify(previousConfig))}.patch.yml`;
  const previousText = JSON.stringify([{ insert: previousVersion === 1 ? [
    { id: 'eva-child', name: fileURLToPath(new URL('../child.js', import.meta.url)), config: { workspace: paths.home, profile: 'eva-000' } },
    { id: 'eva-dm-child', name: fileURLToPath(new URL('../dm-child.js', import.meta.url)), config: { profile: 'eva-000' } },
  ] : [
    { id: 'eva-child', name: fileURLToPath(new URL('../child-entry.js', import.meta.url)), config: { workspace: paths.home, profile: 'eva-000' } },
  ] }], null, 2) + '\n';
  await mkdir(paths.state, { recursive: true });
  await writeFile(join(paths.state, previousName), previousText);
  await manager.start(undefined, 'eva-000');
  const all = await patches(paths.state);
  assert.equal(all.length, 2);
  assert.equal(all.find(patch => patch.name === previousName).text, previousText, 'existing split-entry patch is immutable');
  const nextName = `startup-${installationId}-${hash(JSON.stringify({ workspace: paths.home, profile: 'eva-000', defaultAgentPreset: 'bot-mode', providerDefaultsVersion: 1, dmProtocolVersion: 1, childHostVersion: 7, dmEntry: resolveDmEntry(), memoryEntry: resolveMemoryEntry(), botModeEntry: resolveBotModeEntry(), subconsciousEntry: resolveSubconsciousEntry(), evaPresetHash: preset.digest, desktopExclusions: [] }))}.patch.yml`;
  assert.notEqual(nextName, previousName);
  const next = all.find(patch => patch.name === nextName);
  assert.ok(next, 'composite version chooses a new immutable startup filename');
  assert.equal(next.data[0].insert[0].name, fileURLToPath(new URL('../child.js', import.meta.url)));
  assert.equal(next.data[0].insert.some(row => row.id === 'eva-dm-child'), false);
});

test('changing installed DM entry selects a new immutable overlay without source-relative imports', async t => {
  const { manager } = await fixture(t);
  manager.runtime = { ...runtime, dmEntry: '/synthetic/installed-a/node_modules/@magi/dm/index.js' };
  await manager.start(undefined, 'eva-000');
  await manager.stop(undefined, 'eva-000');
  const paths = manager.paths('eva-000');
  const [before] = await patches(paths.state);
  manager.runtime = { ...runtime, dmEntry: '/synthetic/installed-b/node_modules/@magi/dm/index.js' };
  await manager.start(undefined, 'eva-000');
  const all = await patches(paths.state);
  assert.equal(all.length, 2);
  assert.equal(all.find(row => row.name === before.name).text, before.text);
  const next = all.find(row => row.name !== before.name);
  assert.equal(next.data[0].insert[1].name, manager.runtime.dmEntry);
  assert.equal(before.data[0].insert[1].name, '/synthetic/installed-a/node_modules/@magi/dm/index.js');
});

test('installed memory entry changes create a new immutable overlay without changing the previous patch', async t => {
  const { manager } = await fixture(t);
  manager.runtime = { ...runtime, memoryEntry: '/synthetic/installed-a/node_modules/@magi/memory/index.js' };
  await manager.start(undefined, 'eva-000');
  const paths = manager.paths('eva-000');
  const [before] = await patches(paths.state);
  await manager.stop(undefined, 'eva-000');
  manager.runtime = { ...runtime, memoryEntry: '/synthetic/installed-b/node_modules/@magi/memory/index.js' };
  await manager.start(undefined, 'eva-000');
  const all = await patches(paths.state);
  assert.equal(all.length, 2);
  assert.equal(all.find(row => row.name === before.name).text, before.text);
  const next = all.find(row => row.name !== before.name);
  assert.equal(next.data[0].insert[2].name, manager.runtime.memoryEntry);
  assert.equal(before.data[0].insert[2].name, '/synthetic/installed-a/node_modules/@magi/memory/index.js');
});

test('installed bot-mode entry changes select a new immutable overlay without rewriting its predecessor', async t => {
  const { manager } = await fixture(t);
  manager.runtime = { ...runtime, botModeEntry: '/synthetic/installed-a/node_modules/@magi/bot-mode/index.js' };
  await manager.start(undefined, 'eva-000');
  const paths = manager.paths('eva-000');
  const [before] = await patches(paths.state);
  await manager.stop(undefined, 'eva-000');
  manager.runtime = { ...runtime, botModeEntry: '/synthetic/installed-b/node_modules/@magi/bot-mode/index.js' };
  await manager.start(undefined, 'eva-000');
  const all = await patches(paths.state);
  assert.equal(all.length, 2);
  assert.equal(all.find(row => row.name === before.name).text, before.text);
  const next = all.find(row => row.name !== before.name);
  assert.equal(next.data[0].insert[3].name, manager.runtime.botModeEntry);
  assert.equal(before.data[0].insert[3].name, '/synthetic/installed-a/node_modules/@magi/bot-mode/index.js');
});

test('installed subconscious entry selects a new paused overlay without rewriting its predecessor', async t => {
  const { manager } = await fixture(t);
  manager.runtime = { ...runtime, subconsciousEntry: '/synthetic/installed-a/node_modules/@magi/subconscious/index.js' };
  await manager.start(undefined, 'eva-000');
  const paths = manager.paths('eva-000');
  const [before] = await patches(paths.state);
  await manager.stop(undefined, 'eva-000');
  manager.runtime = { ...runtime, subconsciousEntry: '/synthetic/installed-b/node_modules/@magi/subconscious/index.js' };
  await manager.start(undefined, 'eva-000');
  const all = await patches(paths.state);
  assert.equal(all.length, 2);
  assert.equal(all.find(row => row.name === before.name).text, before.text);
  const next = all.find(row => row.name !== before.name);
  assert.deepEqual(next.data[0].insert[5], { id: 'magi-subconscious-child', name: manager.runtime.subconsciousEntry, config: {} });
  assert.equal(before.data[0].insert[5].name, '/synthetic/installed-a/node_modules/@magi/subconscious/index.js');
});

test('readiness refuses missing native Codex confirmation and releases child ownership', async (t) => {
  const { manager } = await fixture(t);
  await assert.rejects(manager.start(undefined, 'missing-codex'), /codex/i);
  await assert.rejects(access(manager.paths('missing-codex').lock), { code: 'ENOENT' });
  assert.notEqual((await manager.status(undefined, 'missing-codex')).status, 'running');
});
