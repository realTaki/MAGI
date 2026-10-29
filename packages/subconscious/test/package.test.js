import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Config, apply, inject, name } from '../index.js';
import { preset, MEMORY_TOOLS } from '../policy.js';

const read = path => readFile(new URL(path, import.meta.url), 'utf8');
test('portable native bundle resources and conservative config contract', async () => {
  const pkg = JSON.parse(await read('../package.json'));
  assert.equal(pkg.name, '@magi/subconscious'); assert.equal(name, 'magi-subconscious');
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
  for (const file of ['index.js', 'controller.js', 'policy.js', 'cordis.patch.yml', 'icon.svg', 'README.md']) {
    assert.ok(pkg.files.includes(file)); assert.ok(await read(`../${file}`));
  }
  for (const locale of ['en', 'zh']) JSON.parse(await read(`../locale/${locale}.json`));
  assert.ok(inject.includes('magiMainSession')); assert.ok(inject.includes('magiMemory'));
  const defaults = Config.parse({});
  assert.equal(defaults.enabled, false); assert.equal(defaults.recurring, false);
  assert.equal(defaults.maxTurns, 3); assert.equal(defaults.maxSteps, 6); assert.equal(defaults.maxTokens, 1200);
  assert.equal(defaults.heartbeatMinutes, 30); assert.equal(defaults.dailyTurnLimit, 6); assert.equal(defaults.dailyStepLimit, 12);
  assert.throws(() => Config.parse({ enabled: true }));
  assert.throws(() => Config.parse({ enabled: true, runId: 'x', maxTurns: 999 }));
  assert.doesNotMatch(JSON.stringify(preset), /tool-subagent|tool-workflow|tool-schedule|tool-bash|tool-fs|plugin-manager/);
  assert.deepEqual(MEMORY_TOOLS, ['list_memory', 'get_memory', 'save_memory', 'archive_memory']);
});

test('default apply only registers composition/storage/control; teardown owns both', async () => {
  const effects = [], services = new Map(), listeners = new Map();
  let presetDisposed = false, domainClosed = false;
  const ctx = {
    get: name => services.get(name), provide: (name, value) => services.set(name, value),
    effect: body => { effects.push(body()); }, on: (name, listener) => listeners.set(name, listener),
    agentPresets: { register: async value => { assert.equal(value.id, 'subconscious'); return async () => { presetDisposed = true; }; } },
    storageDomain: { open: async spec => {
      let state = spec.global.schema.parse({ ...spec.global.initial, sessionId: 'self' });
      return { global: { get: () => state, set: async value => { state = spec.global.schema.parse(value); } }, close: async () => { domainClosed = true; } };
    } },
  };
  await apply(ctx);
  assert.equal(services.get('magiSubconscious').status().paused, true);
  assert.ok(listeners.has('agent/pre-step')); assert.ok(listeners.has('agent/created'));
  const definitions = new Map(); let mask, guard;
  const scopedTools = {
    restrict: value => { mask = value; return () => { mask = undefined; }; },
    register: definition => { definitions.set(definition.name, definition); return () => definitions.delete(definition.name); },
    guard: value => { guard = value; return () => { guard = undefined; }; },
  };
  const agent = { id: 'self', ctx: { tools: scopedTools, effect: body => body() } };
  listeners.get('agent/created')({ agent });
  assert.deepEqual(mask, { allow: MEMORY_TOOLS });
  assert.deepEqual([...definitions.keys()], ['subconscious_status', 'subconscious_pause', 'subconscious_propose']);
  for (const name of ['subagent', 'subagent_fork', 'workflow', 'schedule_create', 'delete_memory', 'bash', 'plugin_manager']) {
    assert.match(guard({ agent, name }), /restricted/);
  }
  assert.match(guard({ agent, name: 'save_memory' }), /paused/);
  for (const dispose of effects.reverse()) await dispose();
  assert.equal(definitions.size, 0); assert.equal(mask, undefined); assert.equal(guard, undefined);
  assert.equal(domainClosed, true); assert.equal(presetDisposed, true);
});
