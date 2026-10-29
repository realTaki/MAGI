import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
import * as lifecycle from '../child.js';
import * as dm from '../../../packages/dm/index.js';

async function isolatedChild() {
  const source = await readFile(new URL('../child.js', import.meta.url), 'utf8');
  const effects = [], services = new Map(), calls = [];
  const channel = { connected: true, send() { assert.fail('activation must not send IPC'); } };
  const ctx = {
    provide(key, value) { assert.equal(services.has(key), false); services.set(key, value); },
    effect(callback) { effects.push(callback); },
  };
  const apply = runInNewContext(`${source.replace(/^import .*;$/gm, '').replace(/^export /gm, '')}\napply;`, {
    process: channel,
    registerChildView(actual, current) { assert.equal(actual, ctx); calls.push(['view', current]); },
    registerEmbeddedLogin(actual) { assert.equal(actual, ctx); calls.push(['login']); },
    createMessagePreviewReader(actual) { assert.equal(actual, ctx); return () => {}; },
  });
  return { apply, ctx, channel, services, effects, calls };
}

test('child lifecycle and standalone DM advertise independent native service contracts', () => {
  assert.equal(lifecycle.name, 'eva-child');
  assert.equal(dm.name, 'magi-dm');
  assert.ok(dm.inject.includes('magiDmTransport'));
  assert.ok(dm.inject.includes('tools'));
  assert.ok(!lifecycle.inject.includes('tools'), 'lifecycle never owns the DM tool');
  assert.ok(!lifecycle.inject.includes('magiDmTransport'), 'lifecycle provides the transport rather than consuming it');
});

test('child activation provides exact configured identity, workspace and process-owned channel without applying DM', async () => {
  const fixture = await isolatedChild();
  const config = Object.freeze({ workspace: '/synthetic/eva-home', profile: 'eva-000',
    credentialsPath: '/synthetic/credentials.yaml', credentialSharingVersion: 1 });
  fixture.apply(fixture.ctx, config);
  const transport = fixture.services.get('magiDmTransport');
  assert.ok(transport);
  assert.equal(transport.kind, 'child');
  assert.equal(transport.workspace, config.workspace);
  assert.equal(transport.handle, config.profile);
  assert.equal(transport.channel, fixture.channel, 'identity is admitted only through the actual parent process channel');
  assert.deepEqual(Object.keys(transport).sort(), ['channel', 'handle', 'kind', 'workspace']);
  assert.equal(fixture.services.has('magiDm'), false, 'standalone plugin, not EVA, owns the installation');
  assert.equal(fixture.services.has('magiMainSession'), false, 'EVA transport no longer owns main identity');
  assert.ok(lifecycle.inject.includes('magiBotMode'), 'startup consumes independent bot-mode');
  assert.equal(fixture.calls.filter(([name]) => name === 'view').length, 1);
  assert.equal(config.profile, 'eva-000');
});

test('published native entries do not depend on retired union wrappers', async () => {
  const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
  assert.ok(manifest.files.includes('child.js'));
  for (const retired of ['child-entry.js', 'dm-child.js']) assert.ok(!manifest.files.includes(retired));
  const business = JSON.parse(await readFile(new URL('../../../packages/dm/package.json', import.meta.url), 'utf8'));
  assert.equal(business.name, '@magi/dm');
  assert.equal(business.exports['.'], './index.js');
  assert.equal(business.exports['./client'], './client.js');
});
