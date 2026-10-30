import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { z } from 'zod';

// Isolated test process: simulate optional package absence, not a native graph.
test('missing optional native peer retains real Zod activation and honest status', async () => {
  let mode = 'missing';
  const hooks = registerHooks({ resolve(specifier, context, nextResolve) {
    if (specifier !== '@deepseek-ai/schemastery') return nextResolve(specifier, context);
    if (mode === 'missing') throw Object.assign(new Error(
      `Cannot find package '@deepseek-ai/schemastery' imported from ${context.parentURL}`), { code: 'ERR_MODULE_NOT_FOUND' });
    const source = mode === 'dependency-error'
      ? `throw Object.assign(new Error("Cannot find package 'broken-transitive' imported from native"), { code: 'ERR_MODULE_NOT_FOUND' });`
      : 'export default {};';
    return { url: `data:text/javascript,${encodeURIComponent(source)}`, shortCircuit: true };
  } });
  try {
    const { Config, apply, nativeConfigAvailable } = await import('../index.js?optional-missing');
    assert.equal(nativeConfigAvailable, false);
    assert.ok(Config instanceof z.ZodType);
    assert.equal(Config[Symbol.for('schemastery')], undefined);
    assert.equal(Config['~standard'].vendor, 'zod');
    assert.equal(Config.parse({}).enabled, false);
    assert.throws(() => Config.parse({ enabled: true, runId: ' ' }), /human-chosen/);
    assert.throws(() => Config.parse({ dailyStepLimit: 25 }), /24/);
    assert.throws(() => Config.parse({ surprise: true }), /Unrecognized/);
    const effects = [], services = new Map();
    let registration;
    services.set('cordisInspect', { register(value) { registration = value; return () => {}; } });
    const ctx = {
      get: key => services.get(key), provide: (key, value) => services.set(key, value),
      effect: fn => effects.push(fn()), on: () => {},
      agentPresets: { register: async () => () => {} },
      storageDomain: { open: async spec => {
        let state = spec.global.schema.parse(spec.global.initial);
        return { global: { get: () => state, set: async value => { state = spec.global.schema.parse(value); } }, close: async () => {} };
      } },
    };
    await apply(ctx);
    assert.equal(services.get('magiSubconscious').status().paused, true);
    const status = await registration.query('status');
    assert.equal(status.nativeConfigAvailable, false);
    assert.equal(status.enabled, false);
    for (const dispose of effects.reverse()) await dispose();
    mode = 'dependency-error';
    await assert.rejects(import('../index.js?broken-native-dependency'), /broken-transitive/);
    mode = 'malformed-api';
    await assert.rejects(import('../index.js?broken-native-api'), /Schema\.(?:transform|object|boolean) is not a function/);
  } finally { hooks.deregister(); }
});
