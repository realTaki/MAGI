import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, access } from 'node:fs/promises';
import { name, inject } from '../index.js';

const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));

test('DM is an independent native Host/Client plugin, not an EVA bundle', async () => {
  assert.equal(manifest.name, '@magi/dm');
  assert.equal(name, 'magi-dm');
  assert.equal(manifest.exports['.'], './index.js');
  assert.equal(manifest.exports['./client'], './client.js');
  assert.equal(manifest.dsh.bundle, undefined);
  assert.equal(manifest.dsh.client.platform, 'web');
  assert.equal(manifest.dsh.client.immediately, true);
  assert.ok(inject.includes('magiDmTransport'));
  assert.ok(!Object.keys(manifest.dependencies ?? {}).some(key => key.includes('eva')));
  for (const resource of ['index.js', 'client.js', 'dm-host.js', 'dm-plugin.js', 'dm-user.js', 'dm-rpc.js', 'dm-store.js', 'dm-tool.js', 'skills/local-dm/SKILL.md', 'icon.svg', 'README.md', 'locale/en.json', 'locale/zh.json']) {
    await access(new URL(`../${resource}`, import.meta.url));
  }
});
