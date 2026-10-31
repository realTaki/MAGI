// No transpilation/bundling: DSH loads these source modules and resources.
import assert from 'node:assert/strict';
import { globSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const lock = JSON.parse(readFileSync(path.join(root, 'package-lock.json'), 'utf8'));
let checked = 0;
for (const [location, expectedName] of [['apps/eva', '@local/eva'], ['apps/magi-shell', '@local/magi-shell'], ['packages/dm', '@magi/dm'], ['packages/memory', '@magi/memory'], ['packages/bot-mode', '@magi/bot-mode'], ['packages/subconscious', '@magi/subconscious'], ['packages/history-search', '@magi/history-search']]) {
  const dir = path.join(root, location);
  const app = location;
  const pkg = JSON.parse(readFileSync(path.join(dir, 'package.json'), 'utf8'));
  assert.equal(pkg.type, 'module');
  assert.equal(pkg.name, expectedName);
  assert.equal(lock.packages[location].name, pkg.name);
  assert.equal(lock.packages[location].version, pkg.version);
  if (location.startsWith('apps/') || ['packages/memory', 'packages/bot-mode', 'packages/subconscious'].includes(location)) {
    assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
    assert.ok(readFileSync(path.join(dir, 'cordis.patch.yml'), 'utf8').includes(pkg.name));
  } else assert.equal(pkg.dsh.bundle, undefined, 'Integrating bundles mount the reusable DM plugin.');
  for (const entry of pkg.files) {
    assert.ok(!path.isAbsolute(entry) && !entry.split(/[\\/]/).includes('..'), `Unsafe bundle resource: ${entry}`);
    if (entry === 'locale/*.json') {
      for (const language of ['en', 'zh']) JSON.parse(readFileSync(path.join(dir, 'locale', `${language}.json`), 'utf8'));
    } else assert.ok(globSync(entry, { cwd: dir }).length > 0, `Missing bundle resource: ${app}/${entry}`);
  }
  for (const name of readdirSync(dir).filter(name => name.endsWith('.js')).sort()) {
    const result = spawnSync(process.execPath, ['--check', path.join(dir, name)], { stdio: 'inherit' });
    if (result.error) throw result.error;
    assert.equal(result.status, 0, `Syntax check failed: ${app}/${name}`);
    assert.ok(statSync(path.join(dir, name)).isFile());
    checked++;
  }
}
console.log(`DSH manifests/resources and ${checked} source modules checked; no desktop build or package produced.`);
