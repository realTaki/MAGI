import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Config, apply, inject, name, schemaRuntime, nativeConfigAvailable } from './config-runtime.js';
import { preset, MEMORY_TOOLS } from '../policy.js';

const read = path => readFile(new URL(path, import.meta.url), 'utf8');
test('portable native bundle resources and conservative config contract', async () => {
  const pkg = JSON.parse(await read('../package.json'));
  assert.equal(pkg.name, '@magi/subconscious'); assert.equal(name, 'magi-subconscious');
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
  assert.equal(pkg.peerDependencies['@deepseek-ai/schemastery'], '^3.18.5');
  assert.equal(pkg.peerDependenciesMeta['@deepseek-ai/schemastery'].optional, true);
  for (const file of ['index.js', 'controller.js', 'policy.js', 'cordis.patch.yml', 'icon.svg', 'README.md']) {
    assert.ok(pkg.files.includes(file)); assert.ok(await read(`../${file}`));
  }
  for (const locale of ['en', 'zh']) {
    const metadata = JSON.parse(await read(`../locale/${locale}.json`)).meta;
    assert.ok(metadata.title); assert.ok(metadata.description);
  }
  assert.ok(inject.includes('magiMainSession')); assert.ok(inject.includes('magiMemory'));
  const defaults = Config({});
  assert.equal(defaults.enabled, false); assert.equal(defaults.recurring, false);
  assert.equal(defaults.maxTurns, 3); assert.equal(defaults.maxSteps, 6); assert.equal(defaults.maxTokens, 1200);
  assert.equal(defaults.heartbeatMinutes, 30); assert.equal(defaults.dailyTurnLimit, 6); assert.equal(defaults.dailyStepLimit, 12);
  assert.throws(() => Config({ enabled: true }));
  assert.throws(() => Config({ enabled: true, runId: 'x', maxTurns: 999 }));
  assert.doesNotMatch(JSON.stringify(preset), /tool-subagent|tool-workflow|tool-schedule|tool-bash|tool-fs|plugin-manager/);
  assert.deepEqual(MEMORY_TOOLS, ['list_memory', 'get_memory', 'save_memory', 'archive_memory']);
});

test('native public Config graph and Standard Schema contract', t => {
  t.diagnostic(`Schema runtime: ${schemaRuntime}`);
  assert.equal(nativeConfigAvailable, true);
  // Exact native isNativeConfigSchema predicate from dsh-app-boot:2440–2451.
  const native = schema => schema?.[Symbol.for('schemastery')] === true &&
    typeof schema.type === 'string' && schema.meta !== null && typeof schema.meta === 'object';
  assert.ok(native(Config)); assert.equal(Config.type, 'transform');
  assert.ok(native(Config.inner)); assert.equal(Config.inner.type, 'object');
  const fields = Config.inner.dict;
  for (const schema of Object.values(fields)) assert.ok(native(schema));
  assert.equal(fields.enabled.meta.default, false);
  assert.equal(fields.runId.meta.default, '');
  assert.equal(fields.recurring.meta.default, false);
  for (const [key, min, max, fallback] of [
    ['dailyTurnLimit', 1, 12, 6], ['dailyStepLimit', 1, 24, 12], ['maxTurns', 1, 3, 3],
    ['maxSteps', 1, 12, 6], ['maxTokens', 128, 2048, 1200], ['cooldownMs', 30000, 3600000, 60000],
    ['heartbeatMinutes', 5, 1440, 30], ['maxRunMs', 0, 86400000, 3600000],
  ]) {
    assert.equal(fields[key].type, 'number');
    assert.deepEqual(fields[key].meta, { step: 1, min, max, default: fallback });
  }
  const standard = Config['~standard'];
  assert.equal(standard.version, 1); assert.equal(standard.vendor, 'schemastery');
  assert.equal(typeof standard.validate, 'function');
  assert.equal(standard.validate({}).value.enabled, false);
  assert.throws(() => Config({ enabled: true, runId: '   ' }));
  assert.throws(() => Config({ surprise: true }));
  const approved = { enabled: true, runId: 'human-approved', recurring: true,
    heartbeatMinutes: 30, dailyTurnLimit: 6, dailyStepLimit: 12 };
  const resolved = standard.validate(approved);
  assert.ok(!resolved.issues);
  for (const [key, value] of Object.entries(approved)) assert.equal(resolved.value[key], value);
});

test('root opens a native-valid DomainSpec without rewriting configured limits', async () => {
  // Direct apply must retain real private Zod validation independently of the
  // optional native graph fixture, before touching any runtime Service.
  await assert.rejects(apply({}, { enabled: true, runId: '   ' }), /human-chosen runId/);
  await assert.rejects(apply({}, { surprise: true }), /Unrecognized key/);
  await assert.rejects(apply({}, { maxSteps: 1.5 }), error =>
    error.issues.some(issue => issue.path[0] === 'maxSteps' && issue.expected === 'int'));
  await assert.rejects(apply({}, { dailyStepLimit: 25 }), /24/);
  // Exact public UNIT_NAME_RE from @deepseek-ai/dsh-storage; no backend IO.
  const unitName = /^[a-z][a-z0-9_]*$/;
  assert.equal(unitName.test('magi-subconscious'), false);
  const boundary = new Error('fake boundary reached');
  const config = { enabled: true, runId: 'existing-authorization', recurring: true,
    maxTurns: 2, maxSteps: 4, dailyTurnLimit: 3, dailyStepLimit: 8 };
  const before = structuredClone(config);
  const ctx = {
    get: () => undefined, effect: () => {},
    agentPresets: { register: async () => () => {} },
    storageDomain: { open: async spec => {
      assert.equal(spec.name, 'subconscious'); assert.match(spec.name, unitName);
      assert.equal(spec.version, 1); assert.equal(spec.layout, 'single');
      assert.deepEqual(spec.tables, {});
      assert.equal(spec.global.schema.safeParse(null).success, false);
      assert.deepEqual(spec.global.schema.parse(spec.global.initial), spec.global.initial);
      assert.equal(spec.global.initial.paused, true);
      throw boundary;
    } },
  };
  await assert.rejects(apply(ctx, config), error => error === boundary);
  assert.deepEqual(config, before);
});

test('default apply only registers composition/storage/control; teardown owns both', async () => {
  const effects = [], services = new Map(), listeners = new Map();
  let presetDisposed = false, domainClosed = false, inspectDisposed = false, inspectRegistration;
  services.set('cordisInspect', { register(registration) {
    inspectRegistration = registration;
    return () => { inspectDisposed = true; };
  } });
  const ctx = {
    get: name => services.get(name), provide: (name, value) => services.set(name, value),
    effect: body => { effects.push(body()); }, on: (name, listener) => listeners.set(name, listener),
    agentPresets: { register: async value => { assert.equal(value.id, 'subconscious'); return async () => { presetDisposed = true; }; } },
    storageDomain: { open: async spec => {
      assert.match(spec.name, /^[a-z][a-z0-9_]*$/, 'native storage UNIT_NAME_RE');
      let state = spec.global.schema.parse({ ...spec.global.initial, sessionId: 'self' });
      return { global: { get: () => state, set: async value => { state = spec.global.schema.parse(value); } }, close: async () => { domainClosed = true; } };
    } },
  };
  await apply(ctx);
  assert.equal(services.get('magiSubconscious').status().paused, true);
  assert.equal(inspectRegistration.manifest.id, 'MagiSubconscious');
  assert.deepEqual(inspectRegistration.manifest.methods.map(value => value.name), ['status']);
  const status = await inspectRegistration.query('status', {});
  assert.equal(status.paused, true); assert.equal(status.enabled, false);
  assert.equal(status.runId, ''); assert.equal(status.reason, 'not-enabled');
  assert.equal(status.dailyTurnLimit, 6); assert.equal(status.dailyStepLimit, 12);
  for (const key of ['proposals', 'evidence', 'workspace', 'messages', 'cursor']) assert.ok(!(key in status));
  await services.get('magiSubconscious').pause('error: private Session message');
  assert.equal((await inspectRegistration.query('status')).reason, 'error');
  assert.doesNotMatch(JSON.stringify(await inspectRegistration.query('status')), /private Session message/);
  await assert.rejects(inspectRegistration.query('pause'), /Unknown/);
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
  assert.equal(domainClosed, true); assert.equal(presetDisposed, true); assert.equal(inspectDisposed, true);
});
