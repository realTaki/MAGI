import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, realpath, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { EvaRuntime } from '../runtime.js';

const fake = fileURLToPath(new URL('./fake-cli.js', import.meta.url));
const runtime = { executable: process.execPath, cli: fake, runtimeDir: '/unused', supportDir: '/unused' };
const secret = 'do-not-leak';

async function fixture(t) {
  const root = await realpath(tmpdir());
  const base = await mkdtemp(join(root, 'eva-navigation-test-'));
  const manager = new EvaRuntime(runtime, { homeRoot: base, startupTimeout: 3000, stopTimeout: 1000 });
  t.after(async () => {
    await manager.dispose();
    assert.equal(resolve(base), base);
    assert.ok(base.startsWith(join(root, 'eva-navigation-test-')));
    await rm(base, { recursive: true });
  });
  return { base, manager };
}

function publicSnapshot(value) {
  const text = JSON.stringify(value);
  assert.ok(!text.includes(secret), 'public results must not include child login token');
  assert.ok(!text.includes('launchUrl'), 'public results must not include child launch URL');
  assert.ok(!text.includes('?token='), 'public results must not include token query');
}

test('launchUrl uses canonical home map key and is available only while running', async (t) => {
  const { base, manager } = await fixture(t);
  assert.equal(manager.launchUrl('eva-000'), undefined);
  const started = await manager.start(base, 'eva-000');
  publicSnapshot(started);
  assert.equal(started.mainSessionId, 'fake-main-session');
  assert.equal(started.origin, 'http://127.0.0.1:12345');
  const home = manager.paths('eva-000').home;
  assert.ok(manager.nodes.has(home), 'runtime stores nodes by home, not handle or workspace');
  assert.equal(manager.nodes.has('eva-000'), false);
  assert.equal(manager.launchUrl('eva-000'), `http://127.0.0.1:12345/?token=${secret}#eva-main`);
  publicSnapshot(await manager.start(base, 'eva-000'));
  publicSnapshot(await manager.status(base, 'eva-000'));
  publicSnapshot(await manager.status());
  const stopped = await manager.stop(base, 'eva-000');
  publicSnapshot(stopped);
  assert.equal(stopped.status, 'stopped');
  assert.equal(manager.launchUrl('eva-000'), undefined);
  publicSnapshot(await manager.status());
});

test('launchUrl rejects malformed handles without disclosing private navigation', async (t) => {
  const { base, manager } = await fixture(t);
  await manager.start(base, 'eva-000');
  for (const handle of [undefined, null, '../eva-000', '/tmp/escape', '', 'EVA', 'a/b']) {
    assert.throws(() => manager.launchUrl(handle), (error) => {
      assert.ok(!String(error).includes(secret));
      return /Node name/.test(error.message);
    });
  }
  assert.equal(manager.launchUrl('unmanaged'), undefined);
});

for (const handle of ['nav-origin-mismatch', 'nav-missing-main', 'nav-bad-hash',
  'nav-credentials', 'nav-external-protocol', 'nav-external-host', 'nav-path']) {
  test(`rejects malformed navigation readiness ${handle} without leaking credentials`, async (t) => {
    const { base, manager } = await fixture(t);
    await assert.rejects(manager.start(base, handle), (error) => {
      assert.match(error.message, /navigation readiness/);
      assert.ok(!String(error).includes(secret));
      assert.ok(!String(error).includes('?token='));
      return true;
    });
    assert.equal(manager.launchUrl(handle), undefined);
    publicSnapshot(await manager.status(base, handle));
    publicSnapshot(await manager.status());
    await assert.rejects(access(manager.paths(handle).lock), { code: 'ENOENT' });
  });
}
