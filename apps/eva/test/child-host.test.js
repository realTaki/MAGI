import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { DEFAULT_EVA_PRESET, CHILD_HOST_VERSION, resolveDmEntry, resolveMemoryEntry, readEvaPreset, desktopRoleExclusions, createChildHostPatch } from '../child-host.js';

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
  assert.equal(DEFAULT_EVA_PRESET, 'eva');
  assert.equal(CHILD_HOST_VERSION, 4);
  assert.deepEqual(createChildHostPatch(options), [{ insert: [
    { id: 'eva-child', name: modulePath('child'), config: { workspace: options.workspace, profile: options.profile } },
    { id: 'magi-dm-child', name: resolveDmEntry(), config: {} },
    { id: 'magi-memory-child', name: resolveMemoryEntry(), config: {} },
    { id: 'eva-agent-preset', name: 'cordis:include', config: { path: options.presetPath } },
  ] }, {
    id: 'agent-preset-registry', name: '@deepseek-ai/dsh-agent-preset-registry', config: { default: 'eva' },
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
  assert.deepEqual(overlay[0].insert[3], { id: 'eva-agent-preset', name: 'cordis:include', config: { path: options.presetPath } });
  assert.equal(overlay[0].insert.length, 4, 'explicit installed root works even with older selected EVA bundles');
  assert.equal(overlay[0].insert.some(row => row.id === 'eva-dm-child'), false);
  assert.deepEqual(overlay[3], { id: 'credentials', name: '@deepseek-ai/dsh-credentials-local', disabled: false,
    config: { path: credentialsPath, watch: true } });
  assert.deepEqual(overlay[4], { id: 'magi-dm', disabled: true }, 'disable an existing optional bundle row without asserting or renaming its package');
  assert.deepEqual(overlay[5], { id: 'magi-memory', disabled: true });
  assert.deepEqual(overlay.slice(6), desktopRows);
  assert.deepEqual(exclusions, desktopRows, 'caller-owned role selections are not mutated');
  assert.deepEqual(createChildHostPatch({ ...options, desktopExclusions: exclusions }).slice(5), desktopRows);
});

test('child Host inserts no desktop management or permission/default-model overrides', () => {
  for (const credentialsPath of [undefined, '/synthetic/credentials.yaml']) {
    const overlay = createChildHostPatch({ ...options, credentialsPath });
    const rows = [...overlay.slice(1), ...overlay[0].insert];
    assert.ok(rows.every(row => !['@local/magi-shell', '@local/eva'].includes(row.name)));
    assert.ok(rows.every(row => !/permission|approval|sandbox|default-model/.test(`${row.id} ${row.name}`)));
    assert.equal(JSON.stringify(overlay).includes('danger-full-access'), false);
    assert.deepEqual(overlay[1].config, { default: 'eva' }, 'native saved defaults remain native registry state');
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

test('EVA ships an independent full Creator capability snapshot with a content-addressed digest', async () => {
  const { text, digest } = await readEvaPreset();
  assert.equal(text, await readFile(new URL('../eva-preset.yml', import.meta.url), 'utf8'));
  assert.equal(digest, createHash('sha256').update(text).digest('hex').slice(0, 16));
  assert.match(digest, /^[a-f0-9]{16}$/);
  assert.deepEqual(await readEvaPreset(), { text, digest });
  assert.match(text, /^- id: preset-eva\n\s+name: '@deepseek-ai\/dsh-agent-preset'\n\s+config:\n\s+id: eva$/m);
  assert.doesNotMatch(text, /^\s*(?:extends:|id: preset-cordis|id: cordis$|- insert:)/m);
  const entries = [...text.matchAll(/^\s+- id: ([a-z0-9-]+)\n\s+name: '?([^'\r\n]+)'?$/gm)]
    .map(([, id, name]) => [id, name]);
  const native = (id, name = id) => [id, `@deepseek-ai/dsh-${name}`];
  assert.deepEqual(entries, [
    native('persona'), native('agent-instructions'), native('time-context'), native('tool-bash'), native('tool-pwsh'),
    native('tool-fs'), native('tool-fs-search'), native('tool-jobs'), native('tool-schedule'), native('command-goal'), native('tool-goal'),
    ['planning', 'cordis:group'], native('plan-mode'), ['compaction', 'cordis:group'], native('compaction-basic'),
    native('command-compact'), native('tool-result-pruner', 'compaction-tool-result-pruner'), ['delegation', 'cordis:group'],
    native('tool-subagent-control'), native('tool-subagent-list-agents', 'tool-subagent-control/list-agents'),
    native('tool-subagent'), native('tool-subagent-fork', 'tool-subagent'), native('workflow-ptc'), native('tool-workflow'),
    native('tool-ask-user'), native('tool-todo'), native('tool-web'), native('tool-cordis'), native('skill-filesystem'),
    native('tool-skill'), native('present', 'tool-present'), native('tool-plugin-manager', 'plugin-manager/tools'),
    native('tool-plugin-market', 'plugin-market/tools'),
  ]);
});

test('Creator group isolation, native configuration and platform guards survive the EVA snapshot', async () => {
  const { text } = await readEvaPreset();
  assert.match(text, /- id: planning\n\s+name: cordis:group\n\s+group: true\n\s+isolate:\n\s+planMode: true\n\s+config:/);
  assert.match(text, /- id: compaction\n\s+name: cordis:group\n\s+group: true\n\s+isolate:\n\s+compaction: true\n\s+toolResultPruner: true\n\s+config:/);
  assert.match(text, /- id: delegation\n\s+name: cordis:group\n\s+group: true\n\s+isolate:\n\s+workflowEngine: true\n\s+config:/);
  for (const fragment of [
    'You are a coding agent powered by the {{model}} model.', 'maxBytes: 65536', 'sampleOverCapGlobResults: false',
    'thresholdChars: 8192', 'headChars: 4096', 'tailChars: 1024', 'provider: spawn', 'toolName: subagent',
    'modelSelectionSettings: true', 'provider: fork', 'toolName: subagent_fork', 'allowParallelInProgress: true',
    'fetch: true', 'searchTimeoutMs: 60000', 'until the user approves your plan through exit_plan_mode',
  ]) assert.ok(text.includes(fragment), `preserve Creator configuration: ${fragment}`);
  const shellExpression = id => text.match(new RegExp(`- id: ${id}\\n\\s+name: [^\\n]+\\n\\s+disabled: !!js ([^\\n]+)`))[1];
  for (const platform of ['win32', 'darwin', 'linux']) {
    assert.equal(runInNewContext(shellExpression('tool-bash'), { process: { platform } }), platform === 'win32');
    assert.equal(runInNewContext(shellExpression('tool-pwsh'), { process: { platform } }), platform !== 'win32');
  }
});

test('copied preset resolves native skill assets using the profile anchor, not copied YAML baseUrl', async () => {
  const { text } = await readEvaPreset();
  const expression = text.match(/^\s+- !!js (.+)$/m)[1];
  const anchor = '/synthetic/profiles/eva-000/package.json';
  const packagePath = '/synthetic/native/node_modules/@deepseek-ai/dsh-agent-preset/package.json';
  const requests = [];
  const skills = runInNewContext(expression, {
    ctx: { get(name) { assert.equal(name, 'profileContext'); return { installAnchor: anchor }; } },
    process: { getBuiltinModule(name) {
      if (name === 'node:path') return { dirname, join };
      assert.equal(name, 'node:module');
      return { createRequire(base) {
        assert.equal(base, anchor);
        return { resolve(name) { requests.push(name); return packagePath; } };
      } };
    } },
  });
  assert.equal(skills, join(dirname(packagePath), 'skills'));
  assert.deepEqual(requests, ['@deepseek-ai/dsh-agent-preset/package.json']);
  assert.doesNotMatch(expression, /\bbaseUrl\b/);
});
