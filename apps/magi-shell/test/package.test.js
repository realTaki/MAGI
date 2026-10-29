import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

const root = new URL('../', import.meta.url);
const resource = (path) => readFile(new URL(path, root), 'utf8');

test('single MAGI Shell package exports and distributes all runtime and localized display resources', async () => {
  const pkg = JSON.parse(await resource('package.json'));
  assert.equal(pkg.name, '@local/magi-shell');
  assert.equal(pkg.type, 'module');
  assert.equal(pkg.private, true);
  assert.deepEqual(pkg.dependencies, { '@magi/bot-mode': 'file:../../packages/bot-mode' });
  assert.deepEqual(pkg.bundledDependencies, ['@magi/bot-mode']);
  assert.equal(pkg.scripts, undefined);
  assert.equal(pkg.exports['.'], './index.js');
  assert.equal(pkg.exports['./client'], './client.js');
  assert.equal(pkg.exports['./icon'], './icon.svg');
  assert.equal(pkg.exports['./locale/*.json'], './locale/*.json');
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
  assert.equal(pkg.dsh.client.platform, 'web');
  assert.equal(pkg.dsh.client.immediately, true);
  assert.deepEqual(pkg.dsh.client.inject, ['@deepseek-ai/dsh-client-ui-sidebar', '@deepseek-ai/dsh-client-ui-workspace']);
  for (const file of ['index.js', 'main-session.js', 'client.js', 'cordis.patch.yml', 'icon.svg', 'README.md']) {
    assert.ok(pkg.files.includes(file), `${file} must be distributed`);
    assert.ok((await resource(file)).length, `${file} must exist and not be empty`);
  }
  assert.equal(pkg.files.includes('main-frame.js'), false, 'no parent frame helper is distributed');
  assert.ok(pkg.files.includes('locale/*.json'));
  for (const language of ['en', 'zh']) {
    const dictionary = JSON.parse(await resource(`locale/${language}.json`));
    assert.equal(dictionary.meta.title, 'MAGI Shell');
    assert.ok(dictionary.meta.description);
  }
  const icon = await resource('icon.svg');
  assert.match(icon, /^<svg /);
  assert.match(icon, /<\/svg>\s*$/);
  assert.ok(Buffer.byteLength(icon) < 256 * 1024);
  assert.doesNotMatch(icon, /<script|href=|<foreignObject|https?:\/\/(?!www\.w3\.org\/2000\/svg)/i);
  assert.match(icon, /M15 39V23L23 33L31 23V39/, 'preserve the original workbench M monogram');
  assert.match(icon, /M39 22H50L46 18M50 22L46 26M50 40H39L43 36M39 40L43 44/, 'preserve the original switch arrows');
  const patch = await resource('cordis.patch.yml');
  assert.match(patch, /@local\/magi-shell/);
  assert.match(patch, /id: magi-bot-mode[\s\S]*name: '@magi\/bot-mode'[\s\S]*role: parent[\s\S]*id: magi-shell/);
  assert.doesNotMatch(patch, /@local\/(?:magi-workbench|eva-main-bot|eva)(?![a-z-])/);
});

test('one Client module exposes the merged service union, not two independently registered modules', async () => {
  const source = await resource('client.js');
  const loads = [];
  const page = { __ModuleLoader__: { load(options) { loads.push(options); } } };
  runInNewContext(source, { window: page });
  assert.equal(loads.length, 1);
  assert.equal(loads[0].id, '@local/magi-shell');
  assert.equal((source.match(/__ModuleLoader__\.load\s*\(/g) ?? []).length, 1);
  const plugin = loads[0].factory(key => { assert.equal(key, 'react'); return {}; });
  assert.equal(plugin.name, 'magi-shell');
  assert.deepEqual(Array.from(plugin.inject), ['slots', 'locale', 'uiWorkspace', 'layout']);
  assert.equal(typeof plugin.apply, 'function');
  assert.doesNotMatch(source, /@local\/(?:magi-workbench|eva-main-bot)/);
});

test('merged Host remains functional without owning EVA runtime or adding model tools', async () => {
  const source = await resource('index.js');
  assert.doesNotMatch(source, /EvaRuntime|manager\.dispose|tools\.register|process\.kill/);
  assert.doesNotMatch(source, /registerParentFrame|main-frame\.js|authenticatedUrl|authorizeIndex|webApp|webServer/);
  assert.doesNotMatch(await resource('client.js'), /startSession\(|openWorkspace\(|connectWorkspace\(|forkSession\(|replaceState\(/);
});
