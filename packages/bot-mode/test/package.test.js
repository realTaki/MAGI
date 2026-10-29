import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { apply, name, inject } from '../index.js';

const read = file => readFile(new URL(`../${file}`, import.meta.url), 'utf8');
function fixture() {
  const services = new Map(), cleanups = [];
  const ctx = {
    get: key => services.get(key),
    provide(key, value) {
      assert.ok(!services.has(key)); services.set(key, value);
      cleanups.push(() => services.delete(key));
    },
    effect(callback) { cleanups.push(callback()); },
    systemPrompt: { section() { return () => {}; } },
  };
  return { ctx, services, dispose() { for (const cleanup of cleanups.reverse()) cleanup?.(); } };
}
const child = { role: 'eva-child', workspace: '/synthetic/node', profile: 'eva-000' };

test('standalone bundle ships all runtime exports, original icon and localized metadata', async () => {
  const pkg = JSON.parse(await read('package.json'));
  assert.equal(pkg.name, '@magi/bot-mode');
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
  assert.ok(pkg.files.includes('*.js'));
  assert.equal(pkg.exports['./preset'], './presets/bot-preset.yml');
  assert.equal(pkg.exports['./presets'], './presets.js');
  assert.equal(pkg.exports['./preset-loader'], './preset-loader.js');
  assert.equal(pkg.peerDependencies['@deepseek-ai/cordis-plugin-include'], '*');
  assert.equal(pkg.peerDependenciesMeta['@deepseek-ai/cordis-plugin-include'].optional, true);
  assert.equal(pkg.peerDependencies['@deepseek-ai/dsh-agent-preset'], '*');
  assert.equal(pkg.peerDependenciesMeta['@deepseek-ai/dsh-agent-preset'].optional, true);
  assert.ok(pkg.files.includes('presets/bot-preset.yml'));
  assert.match(await read('presets/bot-preset.yml'), /id: bot-mode/);
  for (const key of ['.', './parent', './main-session', './parent-main-session', './presets']) {
    assert.ok(Object.keys(await import(new URL(`../${pkg.exports[key]}`, import.meta.url))).length);
  }
  assert.match(await read('cordis.patch.yml'), /id: magi-bot-mode/);
  assert.match(await read('cordis.patch.yml'), /role: parent/);
  for (const locale of ['en', 'zh']) {
    const { meta } = JSON.parse(await read(`locale/${locale}.json`));
    assert.ok(meta.title && meta.description);
  }
  assert.match(await read('icon.svg'), /<svg/);
  assert.equal(pkg.dependencies, undefined);
  assert.equal(name, 'magi-bot-mode');
  assert.ok(inject.includes('systemPrompt'), 'native Cordis requires declaring the prompt service dependency');
  for (const integration of ['credentials', 'llm', 'appReady', 'appExit', 'connection', 'tools', 'agentPresets']) assert.ok(!inject.includes(integration));
});

test('activation/current are mutation-free; startup and optional read-only identity are independent services', async () => {
  const f = fixture();
  await apply(f.ctx, child);
  const main = f.services.get('magiMainSession'), startup = f.services.get('magiBotMode');
  assert.deepEqual(Object.keys(main), ['current']);
  assert.deepEqual(Object.keys(startup), ['initialize']);
  assert.equal(main.current(), undefined);
  assert.ok(Object.isFrozen(main) && Object.isFrozen(startup));
  f.dispose();
  assert.equal(main.current(), undefined);
  assert.equal(f.services.size, 0);
  await assert.rejects(startup.initialize(), /disposed/);
});

test('duplicate owners fail rather than replace main identity or transport', async () => {
  const f = fixture();
  await apply(f.ctx, child);
  const original = f.services.get('magiMainSession');
  await assert.rejects(apply(f.ctx, child), /one Bot mode identity owner/);
  assert.equal(f.services.get('magiMainSession'), original);
  f.dispose();
});

for (const config of [null, [], { role: 'unknown' }, { role: 'parent', profile: 'eva-000' }, { ...child, profile: '../escape' }, { ...child, unexpected: true }]) {
  test(`invalid identity config has no registrations: ${JSON.stringify(config)}`, async () => {
    const f = fixture();
    await assert.rejects(apply(f.ctx, config), /Invalid Bot mode/);
    assert.equal(f.services.size, 0);
  });
}
