import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';

const root = new URL('../../', import.meta.url);
const read = file => readFileSync(new URL(file, root), 'utf8');
test('retired BUS memory stays deleted while native memory remains independent', () => {
  assert.ok(!existsSync(new URL('packages/old-memory/', root)));
  assert.ok(!existsSync(new URL('docs/old-memory.md', root)));
  for (const file of ['worker.ts', 'tools.ts', 'tsconfig.json']) {
    assert.ok(!existsSync(new URL(`packages/memory/${file}`, root)));
  }
  const pkg = JSON.parse(read('packages/memory/package.json'));
  assert.equal(pkg.name, '@magi/memory');
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
  assert.ok(JSON.parse(read('package.json')).workspaces.includes('packages/memory'));
  assert.ok(!JSON.parse(read('package.json')).workspaces.includes('packages/old-memory'));
  // Data-model source remains; deleting the retired worker is not deleting old databases.
  assert.ok(existsSync(new URL('packages/bus/books/memoryBook.ts', root)));
});
