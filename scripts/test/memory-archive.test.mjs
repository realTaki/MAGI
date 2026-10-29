import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';

const root = new URL('../../', import.meta.url);
const read = file => readFileSync(new URL(file, root), 'utf8');
test('retired BUS memory is deleted without reviving it in the legacy launcher', () => {
  assert.ok(!existsSync(new URL('packages/old-memory/', root)));
  assert.ok(!existsSync(new URL('docs/old-memory.md', root)));
  for (const file of ['worker.ts', 'tools.ts', 'tsconfig.json']) {
    assert.ok(!existsSync(new URL(`packages/memory/${file}`, root)));
  }
  const launcher = JSON.parse(read('packages/launcher/package.json'));
  assert.equal(launcher.dependencies['@magi/old-memory'], undefined);
  assert.equal(launcher.dependencies['@magi/memory'], undefined);
  const roles = read('packages/launcher/composition/roles.ts');
  assert.doesNotMatch(roles, /MemoryWorker|@magi\/(?:old-)?memory\//);
  assert.doesNotMatch(roles.match(/HOSTED_NODE_WORKERS = \[([^\]]*)\]/)?.[1] ?? '', /"memory"/);
  const composition = JSON.parse(read('packages/launcher/composition/tsconfig.json'));
  assert.ok(!composition.references.some(ref => ['../../old-memory', '../../memory'].includes(ref.path)));
  const pkg = JSON.parse(read('packages/memory/package.json'));
  assert.equal(pkg.name, '@magi/memory');
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
  assert.ok(JSON.parse(read('package.json')).workspaces.includes('packages/memory'));
  assert.ok(!JSON.parse(read('package.json')).workspaces.includes('packages/old-memory'));
  // Data-model source remains; deleting the retired worker is not deleting old databases.
  assert.ok(existsSync(new URL('packages/bus/books/memoryBook.ts', root)));
});
