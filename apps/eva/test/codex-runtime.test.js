import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { access, mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { EvaRuntime } from '../runtime.js';

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
  const nextName = `startup-${installationId}-${hash(JSON.stringify({ ...previousConfig, providerDefaultsVersion: 1 }))}.patch.yml`;
  assert.notEqual(nextName, previousName);
  assert.ok(all.some(({ name }) => name === nextName), 'provider-default version is part of the immutable startup filename');
});

test('readiness refuses missing native Codex confirmation and releases child ownership', async (t) => {
  const { manager } = await fixture(t);
  await assert.rejects(manager.start(undefined, 'missing-codex'), /codex/i);
  await assert.rejects(access(manager.paths('missing-codex').lock), { code: 'ENOENT' });
  assert.notEqual((await manager.status(undefined, 'missing-codex')).status, 'running');
});
