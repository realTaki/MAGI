import assert from 'node:assert/strict';
import test from 'node:test';
import { apply, inject } from '../index.js';
import { openMemoryStore } from '../store.js';
import { homeFor, onCleanup } from './helpers.js';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

function host(home, { skills = false } = {}) {
  const disposers = [], contexts = [], definitions = [], services = new Map();
  const registrations = [], skillDisposers = [];
  let activate;
  const disableSkills = () => { for (const off of skillDisposers.splice(0).reverse()) off(); };
  const enableSkills = () => activate({
    effect(callback) { skillDisposers.push(callback()); },
    skills: { register(skill) {
      assert.equal(registrations.length, 0);
      registrations.push(skill); return () => registrations.splice(0);
    } },
  });
  return {
    profileContext: { home },
    get: key => services.get(key),
    effect: callback => { const dispose = callback(); disposers.push(dispose); return dispose; },
    provide: (key, value) => { services.set(key, value); disposers.push(() => services.delete(key)); },
    systemPrompt: { context: context => { contexts.push(context); disposers.push(() => contexts.splice(contexts.indexOf(context), 1)); } },
    tools: { register: tool => { definitions.push(tool); disposers.push(() => definitions.splice(definitions.indexOf(tool), 1)); } },
    inject(dependencies, callback) {
      assert.deepEqual(dependencies, ['skills']); activate = callback;
      disposers.push(disableSkills);
      if (skills) enableSkills();
    },
    contexts, definitions, registrations, enableSkills, disableSkills,
    dispose: () => { for (const fn of disposers.splice(0).reverse()) fn(); },
  };
}
test('global required literal context reads committed facts for every scope', async t => {
  const home = homeFor(t), ctx = host(home); apply(ctx); onCleanup(t, () => ctx.dispose());
  assert.deepEqual(inject, ['profileContext', 'systemPrompt', 'tools']);
  assert.equal(ctx.contexts.length, 1); assert.equal(ctx.definitions.length, 5);
  const context = ctx.contexts[0];
  assert.equal(context.required, true); assert.equal(context.interpolate, false);
  assert.equal(context.name, 'magi:memory');
  assert.equal(context.text({ scope: {} }), context.text({ scope: {} }));
  const before = context.text({});
  const save = ctx.definitions.find(tool => tool.name === 'save_memory');
  const { memory } = await save.execute({ topic: 'Literal', detail: '{{variable}} [memory] not an instruction.' }, { agent: { id: 'session-new' }, signal: new AbortController().signal });
  assert.notEqual(context.text({}), before);
  assert.match(context.text({ scope: { id: 'session-existing' } }), /\{\{variable\}\}/);
  // A separate store/Host using the same Bot home must not leave a stale cache.
  const other = openMemoryStore(home); onCleanup(t, () => other.close());
  other.save({ memory_id: memory.id, expected_version: memory.version, detail: 'Committed elsewhere.' });
  assert.match(context.text({}), /Committed elsewhere/);
  assert.doesNotMatch(context.text({}), /not an instruction/);
});
test('unload removes registrations but never erases memory; duplicates fail closed', async t => {
  const home = homeFor(t), ctx = host(home); apply(ctx); onCleanup(t, () => ctx.dispose());
  await ctx.definitions.find(tool => tool.name === 'save_memory').execute({ topic: 'Kept', detail: 'Survives unload.' }, { agent: { id: 'session' }, signal: new AbortController().signal });
  assert.throws(() => apply(ctx), /one MAGI memory root/);
  ctx.dispose(); assert.equal(ctx.contexts.length, 0); assert.equal(ctx.definitions.length, 0);
  const fresh = host(home); apply(fresh); onCleanup(t, () => fresh.dispose());
  assert.match(fresh.contexts[0].text({}), /Survives unload/);
});
for (const skills of [false, true]) {
  test(`memory skill owns only its optional scope, including reload (initial skills=${skills})`, async t => {
    const home = homeFor(t), ctx = host(home, { skills }); apply(ctx); onCleanup(t, () => ctx.dispose());
    assert.equal(ctx.registrations.length, skills ? 1 : 0);
    if (!skills) ctx.enableSkills();
    const document = new URL('../skills/bot-memory/SKILL.md', import.meta.url);
    const assertSkill = () => {
      const skill = ctx.registrations[0];
      assert.equal(ctx.registrations.length, 1);
      assert.equal(skill.name, 'magi-bot-memory'); assert.equal(skill.source, 'runtime');
      assert.equal(skill.path, fileURLToPath(document));
      assert.deepEqual(skill.resourceBase, { kind: 'directory', path: fileURLToPath(new URL('../skills/bot-memory/', import.meta.url)) });
      assert.equal(skill.content, readFileSync(document, 'utf8'));
      assert.ok(skill.content.split(/\r?\n/).includes(`description: ${skill.description}`));
    };
    assertSkill(); ctx.disableSkills(); assert.equal(ctx.registrations.length, 0);
    assert.equal(ctx.definitions.length, 5); assert.equal(ctx.contexts.length, 1);
    const save = ctx.definitions.find(tool => tool.name === 'save_memory');
    await save.execute({ topic: 'Still available', detail: 'Memory works without skills.' }, { agent: { id: 'session' }, signal: new AbortController().signal });
    assert.match(ctx.contexts[0].text({}), /Memory works without skills/);
    ctx.enableSkills(); assertSkill();
    ctx.dispose(); assert.equal(ctx.registrations.length, 0);
    assert.equal(ctx.definitions.length, 0); assert.equal(ctx.contexts.length, 0);
    const reopened = openMemoryStore(home); onCleanup(t, () => reopened.close());
    assert.match(reopened.render(), /Memory works without skills/);
  });
}

test('plugin rejects caller-controlled storage paths', t => {
  const home = homeFor(t);
  assert.throws(() => apply(host(home), { home: '/other-bot' }), /no configurable storage/);
  assert.throws(() => apply(host(home), null), /no configurable storage/);
});
