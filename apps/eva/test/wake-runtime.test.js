import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, realpath, mkdir, readFile, readdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { EvaRuntime } from '../runtime.js';
import { registerParentView, WAKE_PATH } from '../view.js';

// Only the test CLI is spawned; never launch an installed Host.
const runtime = { executable: process.execPath, cli: fileURLToPath(new URL('./fake-cli.js', import.meta.url)),
  runtimeDir: '/unused', supportDir: '/unused' };

test('wake reuses a persisted home under concurrent clicks, survives request abort, and never allocates an unknown node', async (t) => {
  const root = await realpath(tmpdir());
  const homeRoot = await mkdtemp(join(root, 'eva-wake-unit-'));
  const manager = new EvaRuntime(runtime, { homeRoot, startupTimeout: 3000, stopTimeout: 1000 });
  t.after(async () => {
    await manager.dispose();
    assert.ok(resolve(homeRoot).startsWith(join(root, 'eva-wake-unit-')));
    await rm(homeRoot, { recursive: true });
  });
  const home = join(homeRoot, '.dsh-eva-007');
  await mkdir(home);
  await writeFile(join(home, 'user-work.txt'), 'preserve my workspace');
  // Persist real EVA inventory metadata with the fake CLI, then reconstruct it
  // through a fresh manager as a parent Host would after restarting.
  const seed = new EvaRuntime(runtime, { homeRoot, startupTimeout: 3000, stopTimeout: 1000 });
  try {
    await seed.start(undefined, 'eva-007');
    await seed.stop(undefined, 'eva-007');
  } finally { await seed.dispose(); }
  assert.equal((await manager.status()).nodes[0].handle, 'eva-007');
  const browserPeer = {};
  const routes = new Map();
  registerParentView({ connection: { browserPeer, admit: () => ({ peer: browserPeer }),
    fetch: { register(route) { routes.set(route.path, route); } } } }, manager);
  const wake = (handle, signal) => routes.get(WAKE_PATH).fetch(new Request(`http://localhost${WAKE_PATH}?handle=${handle}`,
    { method: 'POST', signal }));
  assert.equal((await wake('eva-unknown')).status, 404);
  assert.deepEqual(await readdir(homeRoot), ['.dsh-eva-007']);
  const abort = new AbortController();
  const a = wake('eva-007', abort.signal);
  const b = wake('eva-007');
  abort.abort();
  const [responseA, responseB] = await Promise.all([a, b]);
  assert.equal(responseA.status, 200);
  assert.equal(responseB.status, 200);
  const node = await responseA.json();
  assert.deepEqual(await responseB.json(), node);
  assert.deepEqual(Object.keys(node).sort(), ['handle', 'mainSessionId', 'status', 'workspace']);
  assert.equal(node.workspace, home);
  assert.equal(node.handle, 'eva-007');
  assert.equal(node.status, 'running');
  const first = await manager.status(undefined, 'eva-007');
  await wake('eva-007');
  assert.equal((await manager.status(undefined, 'eva-007')).pid, first.pid, 'duplicate click must reuse the process');
  assert.equal(await readFile(join(home, 'user-work.txt'), 'utf8'), 'preserve my workspace');
  assert.deepEqual(await readdir(homeRoot), ['.dsh-eva-007']);
  await manager.stop(undefined, 'eva-007');
  assert.equal((await wake('eva-007')).status, 200);
  assert.notEqual((await manager.status(undefined, 'eva-007')).pid, first.pid);
  assert.equal(await readFile(join(home, 'user-work.txt'), 'utf8'), 'preserve my workspace');
  assert.deepEqual(await readdir(homeRoot), ['.dsh-eva-007']);
});
