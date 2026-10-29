import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { runInNewContext } from 'node:vm';
import { readBotPreset, readEvaPresets, DEFAULT_BOT_PRESET, LEGACY_EVA_PRESET } from '../presets.js';

test('bot-mode owns a full Creator-derived capability snapshot with a content-addressed digest', async () => {
  const { text, digest } = await readBotPreset();
  assert.equal(text, await readFile(new URL('../presets/bot-preset.yml', import.meta.url), 'utf8'));
  assert.equal(digest, createHash('sha256').update(text).digest('hex').slice(0, 16));
  assert.match(digest, /^[a-f0-9]{16}$/);
  assert.deepEqual(await readBotPreset(), { text, digest });
  assert.match(text, /^- id: preset-bot-mode\n\s+name: '@deepseek-ai\/dsh-agent-preset'\n\s+config:\n\s+id: bot-mode$/m);
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

test('legacy EVA identity shares exactly one capability source without leaking EVA process defaults', async () => {
  assert.equal(DEFAULT_BOT_PRESET, 'bot-mode');
  assert.equal(LEGACY_EVA_PRESET, 'eva');
  const canonical = await readBotPreset();
  const combined = await readEvaPresets();
  assert.ok(combined.text.startsWith(canonical.text + '\n'));
  assert.notEqual(combined.digest, canonical.digest);
  assert.equal(combined.digest, createHash('sha256').update(combined.text).digest('hex').slice(0, 16));
  const legacy = combined.text.slice(canonical.text.length + 1);
  assert.match(legacy, /^- id: preset-eva\n/);
  assert.match(legacy, /^    id: eva$/m);
  const body = text => text.slice(text.indexOf('    plugins:\n'));
  assert.equal(body(legacy), body(canonical.text));
  assert.equal([...combined.text.matchAll(/^- id: /gm)].length, 2);
  assert.deepEqual(await readEvaPresets(), combined, 'no mutable cached document is shared with callers');
  canonical.text = 'mutated by caller';
  assert.notEqual((await readBotPreset()).text, canonical.text);
  assert.doesNotMatch(combined.text, /openai-codex|credentialsPath|defaultAgentPreset|danger-full-access|@local\/eva|@magi\/(?:dm|memory)/);
});

test('Creator group isolation, native configuration and platform guards survive the Bot snapshot', async () => {
  const { text } = await readBotPreset();
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
  const { text } = await readBotPreset();
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
