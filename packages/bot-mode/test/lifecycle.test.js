import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { apply } from '../index.js';

async function fixture(t, role = 'eva-child') {
  const root = await realpath(tmpdir()), home = await mkdtemp(join(root, 'bot-mode-lifecycle-'));
  const dir = join(home, 'profiles', 'eva-000');
  await mkdir(dir, { recursive: true });
  await mkdir(join(home, 'eva'), { mode: 0o700 });
  const services = new Map(), cleanups = [], calls = [], stored = new Map(), sections = new Map();
  const workspace = { sessionIds: [], async attachSession(id) { calls.push('attach'); this.sessionIds.push(id); } };
  const ctx = {
    profileContext: { home, dir, name: 'eva-000' },
    get: key => services.get(key),
    provide(key, value) { assert.ok(!services.has(key)); services.set(key, value); cleanups.push(() => services.delete(key)); },
    effect(callback) { cleanups.push(callback()); },
    systemPrompt: { section(section) {
      assert.ok(!sections.has(section.name));
      sections.set(section.name, section);
      const off = () => sections.delete(section.name);
      cleanups.push(off);
      return off;
    } },
    workspaceRegistry: { async create() { return workspace; } },
    sessionController: {
      async create({ sessionId, cwd }) { calls.push('create'); stored.set(sessionId, { meta: { id: sessionId, cwd, isSeeded: false }, events: [] }); return { sessionId }; },
      async inspect(id) { assert.ok(stored.has(id)); return structuredClone(stored.get(id)); },
      async rename({ sessionId, title }) { calls.push('rename'); stored.get(sessionId).title = { title, source: { kind: 'user' } }; return { title, seq: 0 }; },
    },
    sessionQuery: { async readTitle(id) { return stored.get(id).title; } },
    sessionPersistence: { async flush() { calls.push('flush'); } },
  };
  const dispose = () => { for (const cleanup of cleanups.splice(0).reverse()) cleanup?.(); };
  t.after(async () => {
    dispose();
    assert.equal(resolve(home), home);
    assert.ok(home.startsWith(join(root, 'bot-mode-lifecycle-')));
    await rm(home, { recursive: true });
  });
  await apply(ctx, role === 'parent' ? { role } : { role, workspace: home, profile: 'eva-000' });
  return { ctx, home, calls, workspace, stored, sections, dispose, startup: services.get('magiBotMode'), main: services.get('magiMainSession') };
}

test('concurrent startup attaches and pins exactly once; successful initialization is cached', async t => {
  const f = await fixture(t);
  assert.deepEqual(f.calls, []);
  const values = await Promise.all(Array.from({ length: 24 }, () => f.startup.initialize()));
  assert.ok(values.every(value => value === values[0]));
  assert.equal(f.main.current(), values[0]);
  assert.deepEqual(f.calls, ['create', 'flush', 'attach', 'rename', 'flush']);
  const calls = [...f.calls];
  assert.equal(await f.startup.initialize(), values[0]);
  assert.deepEqual(f.calls, calls);
  assert.equal(JSON.parse(await readFile(join(f.home, 'eva', 'main-session.json'), 'utf8')).sessionId, values[0].sessionId);
});

test('failed attachment stays unavailable; retry retains the same durable Session', async t => {
  const f = await fixture(t), attach = f.workspace.attachSession;
  f.workspace.attachSession = async () => { throw new Error('attachment unavailable'); };
  await assert.rejects(f.startup.initialize(), /attachment unavailable/);
  assert.equal(f.main.current(), undefined);
  assert.equal(f.stored.size, 1);
  f.workspace.attachSession = attach;
  const current = await f.startup.initialize();
  assert.equal(current.sessionId, [...f.stored.keys()][0]);
  assert.equal(f.calls.filter(value => value === 'create').length, 1);
  assert.equal(f.calls.filter(value => value === 'rename').length, 1);
});

test('unload during the title durability barrier cannot publish or restart identity', async t => {
  const f = await fixture(t);
  let release, entered, flushes = 0;
  const gate = new Promise(resolve => { release = resolve; });
  const waiting = new Promise(resolve => { entered = resolve; });
  f.ctx.sessionPersistence.flush = async () => { if (++flushes === 2) { entered(); await gate; } };
  const starting = f.startup.initialize();
  await waiting;
  assert.equal(f.main.current(), undefined);
  f.dispose(); release();
  assert.equal(await starting, undefined);
  assert.equal(f.main.current(), undefined);
  await assert.rejects(f.startup.initialize(), /disposed/);
  assert.equal(f.stored.size, 1);
});

test('child identity mismatch is rejected before creating or attaching a Session', async t => {
  const f = await fixture(t);
  f.ctx.profileContext.name = 'different-profile';
  await assert.rejects(f.startup.initialize(), /canonical Bot home and profile/);
  assert.equal(f.main.current(), undefined);
  assert.deepEqual(f.calls, []);
});

for (const role of ['parent', 'eva-child']) {
  test(`${role} publishes coordinator policy only with durable fixed identity and removes it on unload`, async t => {
    const f = await fixture(t, role);
    const section = f.sections.get('magi:main-coordinator');
    assert.equal(f.sections.size, 1);
    assert.equal(section.text({ agent: { id: 'not-ready' } }), '');
    const current = await f.startup.initialize();
    assert.match(section.text({ agent: { id: current.sessionId } }), /foreground coordinator/);
    assert.equal(section.text({ agent: { id: 'worker', parentSession: current.sessionId } }), '');
    f.dispose();
    assert.equal(f.sections.size, 0);
    assert.equal(section.text({ agent: { id: current.sessionId } }), '');
  });
}
