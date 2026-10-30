import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readEvaPresets } from '@magi/bot-mode/presets';
import { DEFAULT_EVA_PRESET, CHILD_HOST_VERSION, resolveDmEntry, resolveMemoryEntry, resolveBotModeEntry, resolveSubconsciousEntry, resolveHistoryEntry, readEvaPreset, desktopRoleExclusions, createChildHostPatch } from '../child-host.js';

const modulePath = name => fileURLToPath(new URL(`../${name}.js`, import.meta.url));
const options = Object.freeze({ workspace: '/synthetic/eva-home', profile: 'eva-000', presetPath: '/synthetic/profiles/.eva-assets/eva-000/preset.yml' });
const desktopRows = [
  { id: 'magi-shell', name: '@local/magi-shell', disabled: true },
  { id: 'eva', name: '@local/eva', disabled: true },
];

async function fixture(t) {
  const root = await realpath(tmpdir());
  const profile = await mkdtemp(join(root, 'eva-child-host-test-'));
  t.after(async () => {
    assert.equal(resolve(profile), profile);
    assert.ok(profile.startsWith(join(root, 'eva-child-host-test-')));
    await rm(profile, { recursive: true });
  });
  return { profile, manifest: join(profile, 'package.json') };
}

test('child Host has a separate EVA default and versioned composition', () => {
  assert.equal(DEFAULT_EVA_PRESET, 'bot-mode');
  assert.equal(CHILD_HOST_VERSION, 8);
  assert.deepEqual(createChildHostPatch(options), [{ insert: [
    { id: 'eva-child', name: modulePath('child'), config: { workspace: options.workspace, profile: options.profile } },
    { id: 'magi-dm-child', name: resolveDmEntry(), config: {} },
    { id: 'magi-memory-child', name: resolveMemoryEntry(), config: {} },
    { id: 'magi-bot-mode-child', name: resolveBotModeEntry(), config: { role: 'eva-child', workspace: options.workspace, profile: options.profile } },
    { id: 'eva-agent-preset', name: 'cordis:include', config: { path: options.presetPath } },
    { id: 'magi-subconscious-child', name: resolveSubconsciousEntry(), config: {} },
    { id: 'magi-history-search-child', name: resolveHistoryEntry(), config: {} },
  ] }, {
    id: 'agent-preset-registry', name: '@deepseek-ai/dsh-agent-preset-registry', config: { default: 'bot-mode' },
  }, {
    id: 'llm-pi-ai', name: '@deepseek-ai/dsh-llm-pi-ai', disabled: false, config: { providers: { 'openai-codex': {} } },
  }]);
});

test('sharing preserves child flags and appends credentials before desktop role exclusions', () => {
  const exclusions = Object.freeze(desktopRows.map(row => Object.freeze({ ...row })));
  const credentialsPath = '/synthetic/credentials.yaml';
  const overlay = createChildHostPatch({ ...options, credentialsPath, desktopExclusions: exclusions });
  assert.deepEqual(overlay[0].insert[0].config, {
    workspace: options.workspace, profile: options.profile, credentialsPath, credentialSharingVersion: 1,
  });
  assert.deepEqual(overlay[0].insert[1], { id: 'magi-dm-child', name: resolveDmEntry(), config: {} });
  assert.deepEqual(overlay[0].insert[2], { id: 'magi-memory-child', name: resolveMemoryEntry(), config: {} });
  assert.deepEqual(overlay[0].insert[4], { id: 'eva-agent-preset', name: 'cordis:include', config: { path: options.presetPath } });
  assert.equal(overlay[0].insert.length, 7, 'explicit installed root works even with older selected EVA bundles');
  assert.equal(overlay[0].insert.some(row => row.id === 'eva-dm-child'), false);
  assert.deepEqual(overlay[3], { id: 'credentials', name: '@deepseek-ai/dsh-credentials-local', disabled: false,
    config: { path: credentialsPath, watch: true } });
  assert.deepEqual(overlay[4], { id: 'magi-dm', disabled: true }, 'disable an existing optional bundle row without asserting or renaming its package');
  assert.deepEqual(overlay[5], { id: 'magi-memory', disabled: true });
  assert.deepEqual(overlay[6], { id: 'magi-bot-mode', disabled: true });
  assert.deepEqual(overlay[7], { id: 'magi-bot-mode-preset', disabled: true });
  assert.deepEqual(overlay[8], { id: 'magi-subconscious', disabled: true });
  assert.deepEqual(overlay.slice(9), [...desktopRows, { id: 'magi-history-search', disabled: true }]);
  assert.deepEqual(exclusions, desktopRows, 'caller-owned role selections are not mutated');
  assert.deepEqual(createChildHostPatch({ ...options, desktopExclusions: exclusions }).slice(8), [...desktopRows, { id: 'magi-history-search', disabled: true }]);
});

test('child Host inserts no desktop management or permission/default-model overrides', () => {
  for (const credentialsPath of [undefined, '/synthetic/credentials.yaml']) {
    const overlay = createChildHostPatch({ ...options, credentialsPath });
    const rows = [...overlay.slice(1), ...overlay[0].insert];
    assert.ok(rows.every(row => !['@local/magi-shell', '@local/eva'].includes(row.name)));
    assert.ok(rows.every(row => !/permission|approval|sandbox|default-model/.test(`${row.id} ${row.name}`)));
    assert.equal(JSON.stringify(overlay).includes('danger-full-access'), false);
    assert.deepEqual(overlay[1].config, { default: 'bot-mode' }, 'native saved defaults remain native registry state');
  }
});

test('missing manifest leaves a new child profile unmodified', async t => {
  const { profile, manifest } = await fixture(t);
  assert.deepEqual(await desktopRoleExclusions(profile), []);
  await assert.rejects(readFile(manifest), { code: 'ENOENT' });
});

for (const [bundles, expected] of [
  [[], []],
  [['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app', '@local/unknown'], []],
  [['@local/magi-shell'], [desktopRows[0]]],
  [['@local/eva'], [desktopRows[1]]],
  [['@local/eva', '@local/magi-shell', '@local/eva', '@local/unknown'], desktopRows],
  [['@local/magi-shell-extra', 'file:/synthetic/eva', { name: '@local/magi-shell' }], []],
]) {
  test(`desktop roles exclude only exact selected bundle strings: ${JSON.stringify(bundles)}`, async t => {
    const { profile, manifest } = await fixture(t);
    const text = JSON.stringify({ name: 'user-owned-profile', dsh: { profile: { bundles } }, custom: 'preserve bytes' }, null, 2) + '\n';
    await writeFile(manifest, text);
    assert.deepEqual(await desktopRoleExclusions(profile), expected);
    assert.equal(await readFile(manifest, 'utf8'), text);
  });
}

test('standalone memory and EVA bundle selections keep exactly one active child memory root', async t => {
  const { profile, manifest } = await fixture(t);
  for (const bundles of [['@magi/memory'], ['@local/eva'], ['@local/eva', '@magi/memory']]) {
    await writeFile(manifest, JSON.stringify({ dsh: { profile: { bundles } } }));
    const desktopExclusions = await desktopRoleExclusions(profile);
    const memoryEntry = '/synthetic/installed/node_modules/@magi/memory/index.js';
    const patch = createChildHostPatch({ ...options, desktopExclusions, memoryEntry });
    assert.deepEqual(patch[0].insert.filter(row => row.id === 'magi-memory-child'), [
      { id: 'magi-memory-child', name: memoryEntry, config: {} },
    ]);
    assert.equal(patch.filter(row => row.id === 'magi-memory' && row.disabled).length, 1);
  }
  assert.throws(() => createChildHostPatch({ ...options, memoryEntry: './relative.js' }), /absolute package entry/);
});

test('subconscious is one paused independent child root across selected bundles', async t => {
  const { profile, manifest } = await fixture(t);
  const subconsciousEntry = '/synthetic/installed/node_modules/@magi/subconscious/index.js';
  for (const bundles of [[], ['@magi/subconscious'], ['@local/eva'], ['@local/eva', '@magi/subconscious']]) {
    const text = JSON.stringify({ dsh: { profile: { bundles } } });
    await writeFile(manifest, text);
    const desktopExclusions = await desktopRoleExclusions(profile);
    const patch = createChildHostPatch({ ...options, desktopExclusions, subconsciousEntry });
    assert.deepEqual(patch[0].insert.filter(row => row.id === 'magi-subconscious-child'), [
      { id: 'magi-subconscious-child', name: subconsciousEntry, config: {} },
    ]);
    assert.equal(patch.filter(row => row.id === 'magi-subconscious' && row.disabled).length, bundles.length ? 1 : 0);
    assert.equal(await readFile(manifest, 'utf8'), text, 'bundle selection stays user-owned');
  }
  assert.throws(() => createChildHostPatch({ ...options, subconsciousEntry: './relative.js' }), /absolute package entry/);
});

test('history search has one installed child root and disables the bundled parent row only when selected', async t => {
  const { profile, manifest } = await fixture(t);
  for (const bundles of [[], ['@local/eva']]) {
    const text = JSON.stringify({ dsh: { profile: { bundles } } });
    await writeFile(manifest, text);
    const patch = createChildHostPatch({ ...options, desktopExclusions: await desktopRoleExclusions(profile) });
    assert.deepEqual(patch[0].insert.filter(row => row.id === 'magi-history-search-child'), [
      { id: 'magi-history-search-child', name: resolveHistoryEntry(), config: {} },
    ]);
    assert.equal(patch.filter(row => row.id === 'magi-history-search' && row.disabled).length, bundles.length);
    assert.equal(await readFile(manifest, 'utf8'), text);
  }
  assert.throws(() => createChildHostPatch({ ...options, historyEntry: './relative.js' }), /absolute package entry/);
});

test('role exclusions return independent override objects', async t => {
  const { profile, manifest } = await fixture(t);
  await writeFile(manifest, JSON.stringify({ dsh: { profile: { bundles: ['@local/magi-shell'] } } }));
  const rows = await desktopRoleExclusions(profile);
  rows[0].disabled = false;
  rows.push({ id: 'unrelated' });
  assert.deepEqual(await desktopRoleExclusions(profile), [desktopRows[0]]);
});

test('invalid existing manifest fails closed and is never repaired or overwritten', async t => {
  const { profile, manifest } = await fixture(t);
  for (const text of ['{ invalid JSON', JSON.stringify({ dsh: { profile: { bundles: '@local/magi-shell' } } })]) {
    await writeFile(manifest, text);
    await assert.rejects(desktopRoleExclusions(profile));
    assert.equal(await readFile(manifest, 'utf8'), text);
  }
});

test('selected Shell and standalone bot-mode roots yield exactly one child identity owner', async t => {
  const { profile, manifest } = await fixture(t);
  for (const bundles of [['@local/magi-shell'], ['@magi/bot-mode'], ['@local/magi-shell', '@magi/bot-mode']]) {
    await writeFile(manifest, JSON.stringify({ dsh: { profile: { bundles } } }));
    const desktopExclusions = await desktopRoleExclusions(profile);
    const botModeEntry = '/synthetic/installed/node_modules/@magi/bot-mode/index.js';
    const patch = createChildHostPatch({ ...options, desktopExclusions, botModeEntry });
    assert.deepEqual(patch[0].insert.filter(row => row.id === 'magi-bot-mode-child'), [
      { id: 'magi-bot-mode-child', name: botModeEntry, config: { role: 'eva-child', workspace: options.workspace, profile: options.profile } },
    ]);
    assert.equal(patch.filter(row => row.id === 'magi-bot-mode' && row.disabled).length, 1);
    assert.equal(patch.filter(row => row.id === 'magi-bot-mode-preset' && row.disabled).length, 1);
  }
  assert.throws(() => createChildHostPatch({ ...options, botModeEntry: './relative.js' }), /absolute package entry/);
});

test('model-facing guidance and approval summaries describe shared Bot fallback without resetting choices', async () => {
  const source = await readFile(new URL('../index.js', import.meta.url), 'utf8');
  assert.ok(source.includes('the shared bot-mode preset'));
  assert.ok(source.includes('unless the user saved another default'));
  assert.ok(source.includes('默认共享 bot-mode 预设'));
  assert.ok(source.includes('default to shared bot-mode'));
  assert.doesNotMatch(source, /independent eva preset|默认独立 eva 预设|independent eva mode/);
});

test('EVA caches package-owned canonical and legacy declarations without maintaining a capability fork', async () => {
  assert.deepEqual(await readEvaPreset(), await readEvaPresets());
  const { text } = await readEvaPreset();
  assert.match(text, /^- id: preset-bot-mode$/m);
  assert.match(text, /^- id: preset-eva$/m);
});
