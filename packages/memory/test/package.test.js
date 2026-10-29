import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import test from 'node:test';
const root = new URL('../', import.meta.url);
const read = name => readFileSync(new URL(name, root), 'utf8');
test('independent memory bundle ships its runtime and localized original display resources', () => {
  const pkg = JSON.parse(read('package.json'));
  assert.equal(pkg.name, '@magi/memory'); assert.equal(pkg.type, 'module');
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
  assert.match(read('cordis.patch.yml'), /id: magi-memory/);
  assert.match(read('cordis.patch.yml'), /name: '@magi\/memory'/);
  assert.equal(pkg.dependencies, undefined); assert.equal(pkg.peerDependencies, undefined);
  assert.equal(pkg.scripts.preinstall, undefined); assert.equal(pkg.scripts.postinstall, undefined);
  for (const file of ['index.js', 'tools.js', 'store.js', 'README.md', 'cordis.patch.yml', 'icon.svg']) {
    assert.ok(pkg.files.includes(file)); assert.ok(existsSync(new URL(file, root)));
  }
  assert.equal(pkg.exports['./icon'], './icon.svg');
  assert.equal(pkg.exports['./locale/*.json'], './locale/*.json');
  for (const lang of ['en', 'zh']) {
    const meta = JSON.parse(read(`locale/${lang}.json`)).meta;
    assert.ok(meta.title.length > 2); assert.ok(meta.description.length > 15);
  }
  assert.match(read('icon.svg'), /<svg[^>]+viewBox="0 0 64 64"/);
  assert.ok(Buffer.byteLength(read('icon.svg')) < 256 * 1024);
  assert.doesNotMatch(read('icon.svg'), /<script|<foreignObject|href=/);
  assert.doesNotMatch(read('index.js'), /agent\/request|system-prompt\/assemble|agent\.inject|process\.cwd/);
});
