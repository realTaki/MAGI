import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { ensureManagedUi } from '../../../packages/webapp/ui-build.ts';

function fixture(t) {
  const root = mkdtempSync(path.join(tmpdir(), 'magi-ui-fingerprint-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const dir of ['apps/shell/web/src', 'apps/shell/dist', 'packages/bus', 'profile']) mkdirSync(path.join(root, dir), { recursive: true });
  const source = path.join(root, 'apps/shell/web/src/app.tsx');
  const entry = path.join(root, 'apps/shell/dist/index.html');
  const profile = path.join(root, 'profile/settings');
  writeFileSync(source, 'new API'); writeFileSync(entry, 'old UI calling chat.listChats'); writeFileSync(profile, 'operator data');
  let builds = 0;
  const build = async () => { builds++; writeFileSync(entry, readFileSync(source)); };
  return { root, source, entry, profile, build, count: () => builds };
}

test('existing legacy UI is rebuilt, unchanged source is reused, local edits invalidate it', async t => {
  const f = fixture(t);
  assert.equal(await ensureManagedUi(f.root, f.build), true);
  assert.equal(readFileSync(f.entry, 'utf8'), 'new API');
  assert.equal(await ensureManagedUi(f.root, f.build), false);
  writeFileSync(f.source, 'operator edited UI');
  assert.equal(await ensureManagedUi(f.root, f.build), true);
  assert.equal(readFileSync(f.entry, 'utf8'), 'operator edited UI');
  assert.equal(readFileSync(f.source, 'utf8'), 'operator edited UI');
  assert.equal(readFileSync(f.profile, 'utf8'), 'operator data');
  assert.equal(f.count(), 2);
});

test('shared workspace sources and lock changes invalidate UI, generated output does not', async t => {
  const f = fixture(t);
  await ensureManagedUi(f.root, f.build);
  writeFileSync(path.join(f.root, 'packages/bus/catalog.ts'), 'updated provider catalog');
  assert.equal(await ensureManagedUi(f.root, f.build), true);
  writeFileSync(path.join(f.root, 'package-lock.json'), '{}');
  assert.equal(await ensureManagedUi(f.root, f.build), true);
  mkdirSync(path.join(f.root, 'packages/bus/dist'));
  writeFileSync(path.join(f.root, 'packages/bus/dist/catalog.js'), 'generated');
  assert.equal(await ensureManagedUi(f.root, f.build), false);
  rmSync(f.entry);
  assert.equal(await ensureManagedUi(f.root, f.build), true);
});

test('failed and racing builds cannot certify stale UI', async t => {
  const f = fixture(t);
  await assert.rejects(ensureManagedUi(f.root, async () => { throw new Error('build failed'); }), /build failed/);
  assert.equal(await ensureManagedUi(f.root, f.build), true);
  writeFileSync(f.source, 'changed');
  await assert.rejects(ensureManagedUi(f.root, async () => { await f.build(); writeFileSync(f.source, 'edited during build'); }), /source changed/);
  assert.equal(await ensureManagedUi(f.root, f.build), true);
});
