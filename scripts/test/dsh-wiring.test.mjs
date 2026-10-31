import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import test from 'node:test';
import { collectTests, portableTests } from '../test.mjs';

const root = new URL('../../', import.meta.url);
const read = file => readFileSync(new URL(file, root), 'utf8');
const importSpecifiers = source => [...source.matchAll(/(?:\bfrom\s+|\bimport\s*\(\s*|\brequire\s*\(\s*)['"]([^'"]+)['"]/g)].map(match => match[1]);

test('import closure scan ignores quoted protocol keys such as from', () => {
  assert.deepEqual(importSpecifiers(`
    const fields = ['version', 'from', 'to'];
    import value from './value.js';
    export { value } from './exports.js';
    import('node:fs');
    require('@deepseek-ai/dsh-tools');
  `), ['./value.js', './exports.js', 'node:fs', '@deepseek-ai/dsh-tools']);
});

const product = [['eva', 'apps/eva'], ['magi-shell', 'apps/magi-shell'], ['dm', 'packages/dm'], ['memory', 'packages/memory'], ['bot-mode', 'packages/bot-mode'], ['subconscious', 'packages/subconscious'], ['history-search', 'packages/history-search']];
const magiPackages = ['@magi/dm', '@magi/memory', '@magi/bot-mode', '@magi/subconscious', '@magi/history-search'];

test('default discovery includes every current DSH regression without retired launcher acceptance', () => {
  const actual = collectTests();
  for (const [app, location] of product) {
    for (const name of readdirSync(new URL(`${location}/test/`, root)).filter(name => name.endsWith('.test.js'))) {
      assert.ok(actual.includes(`${location}/test/${name}`), name);
    }
    for (const name of portableTests[app]) assert.ok(collectTests({ portable: true }).includes(`${location}/test/${name}.test.js`));
  }
  assert.equal(new Set(actual).size, actual.length);
  assert.ok(actual.includes('scripts/test/dependency-policy.test.mjs'), 'keep authoritative lock/advisory checks');
  assert.ok(actual.every(file => !file.includes('/dist/') && !file.includes('/integration/')));
  const pkg = JSON.parse(read('package.json'));
  assert.equal(pkg.scripts.test, 'node scripts/check-dsh.mjs && node scripts/test.mjs');
  assert.deepEqual(pkg.workspaces, product.map(([, location]) => location));
  assert.equal(pkg.scripts['test:legacy'], undefined);
  assert.equal(pkg.scripts['verify:legacy'], undefined);
});

test('active bundle import closures cannot depend on retired Shell or legacy workspace packages', () => {
  for (const [, app] of product) {
    const dir = new URL(`${app}/`, root);
    const pkg = JSON.parse(read(`${app}/package.json`));
    if (app.startsWith('apps/') || ['packages/memory', 'packages/bot-mode', 'packages/subconscious'].includes(app)) assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
    for (const name of readdirSync(dir).filter(name => name.endsWith('.js'))) {
      const source = read(`${app}/${name}`);
      // Static ESM imports, re-exports, literal dynamic imports and injected require.
      const imports = importSpecifiers(source);
      for (const specifier of imports) {
        assert.ok(specifier.startsWith('node:') || specifier.startsWith('./') || specifier.startsWith('@deepseek-ai/') || specifier === 'react' || (app === 'packages/subconscious' && specifier === 'zod') || magiPackages.some(name => specifier === name || specifier.startsWith(name + '/')), `${app}/${name}: ${specifier}`);
        if (specifier.startsWith('./')) assert.ok(existsSync(new URL(specifier, dir)), `${app}/${name}: missing ${specifier}`);
      }
      assert.doesNotMatch(source, /apps\/shell|@magi\/(?!(?:dm|memory|bot-mode|subconscious|history-search)(?:['"/\s]|$))|\.\.\/(?:shell|packages)\//, `${app}/${name}`);
    }
    for (const dependency of Object.keys({ ...pkg.dependencies, ...pkg.peerDependencies })) assert.ok(dependency.startsWith('@deepseek-ai/') || (app === 'packages/subconscious' && dependency === 'zod') || (app === 'apps/eva' && magiPackages.includes(dependency)) || (app === 'apps/magi-shell' && dependency === '@magi/bot-mode'), dependency);
  }
});

test('CI validates DSH source and has no standalone packaging, UI build or old EVA IPC step', () => {
  for (const platform of ['linux', 'windows', 'macos']) {
    const workflow = read(`.github/workflows/${platform}-validation.yml`);
    assert.match(workflow, /scripts\/prepare-runtime\.mjs/);
    assert.doesNotMatch(workflow, /apps\/shell/);
    assert.doesNotMatch(workflow, /build:ui|lint:i18n|self-rebuild\.test|electron-builder|AppImage|upload-artifact|client-release/);
    assert.match(workflow, platform === 'windows' ? /run test:portable/ : /npm-cli\.js test/);
  }
});
