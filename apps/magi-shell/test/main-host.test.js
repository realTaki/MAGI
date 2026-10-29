import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, readFile, writeFile, lstat, symlink, link, chmod, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { name, inject, apply as applyShell, initializeParentMain, PARENT_MAIN_PATH } from '../index.js';
import { apply as applyBotMode } from '@magi/bot-mode';

async function apply(ctx) {
  await applyBotMode(ctx, { role: 'parent' });
  ctx.magiBotMode = ctx.get('magiBotMode');
  await applyShell(ctx);
}

async function fixture(t) {
  const root = await realpath(tmpdir());
  const base = await mkdtemp(join(root, 'eva-parent-test-'));
  const home = join(base, 'home');
  const profile = join(home, 'profiles', 'desktop');
  await mkdir(profile, { recursive: true });
  const workspace = join(home, 'workspace');
  const stateDir = join(profile, 'eva-parent-main');
  const marker = join(stateDir, 'main-session.json');
  t.after(async () => {
    assert.equal(resolve(base), base);
    assert.ok(base.startsWith(join(root, 'eva-parent-test-')));
    await rm(base, { recursive: true });
  });
  return { base, home, profile, workspace, stateDir, marker };
}

function mock(paths, stored = new Map()) {
  const calls = [];
  const durable = new Map();
  const routes = new Map();
  const ready = new Set();
  const cleanups = [];
  const projects = new Map();
  const services = new Map();
  const ctx = {
    get(name) { return services.get(name); },
    provide(name, value) {
      assert.ok(!services.has(name), `duplicate service: ${name}`);
      services.set(name, value);
      const cleanup = () => services.delete(name);
      cleanups.push(cleanup);
      return cleanup;
    },
    profileContext: { home: paths.home, dir: paths.profile, name: 'User Profile.With Spaces' },
    appReady: { onReady(callback) { ready.add(callback); return () => ready.delete(callback); } },
    effect(callback) { const cleanup = callback(); if (cleanup) cleanups.push(cleanup); },
    connection: { fetch: { register(route) {
      routes.set(route.path, route);
      cleanups.push(() => routes.delete(route.path));
      return () => routes.delete(route.path);
    } } },
    sessionController: {
      async create(request) {
        calls.push(['create', structuredClone(request)]);
        const mapping = JSON.parse(await readFile(paths.marker, 'utf8'));
        assert.equal(mapping.state, 'reserved');
        assert.equal(mapping.sessionId, request.sessionId);
        assert.equal(request.cwd, paths.workspace);
        assert.equal(stored.has(request.sessionId), false);
        assert.equal('agentPreset' in request, false);
        stored.set(request.sessionId, { meta: { id: request.sessionId, cwd: request.cwd, isSeeded: false }, events: [] });
        return { sessionId: request.sessionId };
      },
      async inspect(id) {
        calls.push(['inspect', id]);
        if (!stored.has(id)) throw new Error('private storage error /secret/token');
        return structuredClone(stored.get(id));
      },
    },
    sessionPersistence: { async flush() {
      calls.push(['flush']);
      for (const [id, value] of stored) durable.set(id, structuredClone(value));
    } },
    workspaceRegistry: { async create(path, title) {
      calls.push(['workspace', path, title]);
      if (!projects.has(path)) {
        const sessionIds = [];
        projects.set(path, { path, sessionIds, async attachSession(id) {
          calls.push(['attach', id]);
          assert.equal(stored.get(id)?.meta.cwd, path);
          assert.ok(durable.has(id), 'Session must be durable before attachment');
          if (!sessionIds.includes(id)) sessionIds.push(id);
        } });
      }
      return projects.get(path);
    } },
  };
  return { ctx, calls, stored, durable, routes, ready, projects,
    async start() { await Promise.all([...ready].map(callback => callback())); },
    dispose() { for (const cleanup of cleanups.reverse()) cleanup(); },
    get creates() { return calls.filter(([kind]) => kind === 'create'); },
  };
}

async function mapping(paths) { return JSON.parse(await readFile(paths.marker, 'utf8')); }
async function missing(path) { await assert.rejects(lstat(path), { code: 'ENOENT' }); }

async function seed(paths, extra = {}) {
  await mkdir(paths.workspace, { recursive: true });
  await mkdir(paths.stateDir, { mode: 0o700 });
  const value = { version: 2, state: 'ready', profile: 'parent-main', workspace: paths.workspace,
    originalWorkspace: paths.workspace, sessionId: 'session-fixed-parent', ...extra };
  await writeFile(paths.marker, JSON.stringify(value), { mode: 0o600 });
  return value;
}

test('consolidated Host export has only the native dependencies needed for initialization', () => {
  assert.equal(name, 'magi-shell');
  assert.deepEqual(inject, ['appReady', 'profileContext', 'connection', 'magiBotMode', 'sessionQuery']);
  assert.equal(PARENT_MAIN_PATH, '/api/eva/parent-main');
});

test('creates its own workspace and reserved ordinary Session without adopting, renaming, or deleting other conversations', async t => {
  const paths = await fixture(t);
  const other = { meta: { id: 'session-other', cwd: paths.home, isSeeded: false }, events: [{ text: 'keep me' }] };
  const nativeSameWorkspace = { meta: { id: 'session-native', cwd: paths.workspace, isSeeded: false }, events: [] };
  const m = mock(paths, new Map([['session-other', other], ['session-native', nativeSameWorkspace]]));
  const bot = await initializeParentMain(m.ctx);
  assert.deepEqual(bot, { handle: 'dsh-main', kind: 'parent', status: 'running', workspace: paths.workspace, mainSessionId: m.creates[0][1].sessionId });
  assert.equal(m.creates.length, 1);
  assert.notEqual(bot.mainSessionId, 'session-native');
  assert.deepEqual(m.stored.get('session-other'), other);
  assert.deepEqual(m.stored.get('session-native'), nativeSameWorkspace);
  assert.equal((await mapping(paths)).state, 'ready');
  assert.equal((await lstat(paths.stateDir)).mode & 0o077, 0);
  assert.equal((await lstat(paths.marker)).mode & 0o077, 0);
  assert.deepEqual(m.projects.get(paths.workspace).sessionIds, [bot.mainSessionId]);
});

test('restart and repeated activation reuse exactly the durable identity and preserve history', async t => {
  const paths = await fixture(t);
  const first = mock(paths);
  const bot = await initializeParentMain(first.ctx);
  first.stored.get(bot.mainSessionId).events.push({ text: 'preserve history' });
  await first.ctx.sessionPersistence.flush();
  const second = mock(paths, structuredClone(first.durable));
  assert.deepEqual(await initializeParentMain(second.ctx), bot);
  assert.deepEqual(await initializeParentMain(second.ctx), bot);
  assert.equal(second.creates.length, 0);
  assert.deepEqual(second.stored.get(bot.mainSessionId).events, [{ text: 'preserve history' }]);
});

test('concurrent initialization uses one reservation and one Session', async t => {
  const paths = await fixture(t);
  const m = mock(paths);
  const values = await Promise.all(Array.from({ length: 24 }, () => initializeParentMain(m.ctx)));
  assert.equal(new Set(values.map(value => value.mainSessionId)).size, 1);
  assert.equal(m.creates.length, 1);
  assert.equal((await mapping(paths)).state, 'ready');
});

test('competing Host contexts cannot create duplicate fixed Sessions', async t => {
  const paths = await fixture(t);
  const store = new Map();
  const first = mock(paths, store);
  const second = mock(paths, store);
  const results = await Promise.allSettled([initializeParentMain(first.ctx), initializeParentMain(second.ctx)]);
  assert.ok(results.some(result => result.status === 'fulfilled'));
  assert.equal(first.creates.length + second.creates.length, 1);
  assert.equal(store.size, 1);
  const restart = mock(paths, store);
  assert.equal((await initializeParentMain(restart.ctx)).mainSessionId, (await mapping(paths)).sessionId);
  assert.equal(restart.creates.length, 0);
});

test('controller failure leaves a reservation and fails closed on retry instead of creating duplicates', async t => {
  const paths = await fixture(t);
  const m = mock(paths);
  m.ctx.sessionController.create = async () => { throw new Error('create failed /secret'); };
  await assert.rejects(initializeParentMain(m.ctx), /create failed/);
  const reserved = await mapping(paths);
  assert.equal(reserved.state, 'reserved');
  const restart = mock(paths);
  await assert.rejects(initializeParentMain(restart.ctx), /refusing replacement/);
  assert.equal(restart.creates.length, 0);
  assert.deepEqual(await mapping(paths), reserved);
});

test('flush failure can recover the same existing reserved identity after restart', async t => {
  const paths = await fixture(t);
  const first = mock(paths);
  first.ctx.sessionPersistence.flush = async () => { throw new Error('flush failed'); };
  await assert.rejects(initializeParentMain(first.ctx), /flush failed/);
  const reserved = await mapping(paths);
  assert.equal(reserved.state, 'reserved');
  const restart = mock(paths, first.stored);
  assert.equal((await initializeParentMain(restart.ctx)).mainSessionId, reserved.sessionId);
  assert.equal(restart.creates.length, 0);
  assert.equal((await mapping(paths)).state, 'ready');
});

test('an alternate identity returned by create is never adopted', async t => {
  const paths = await fixture(t);
  const m = mock(paths);
  m.ctx.sessionController.create = async () => ({ sessionId: 'session-unrelated' });
  await assert.rejects(initializeParentMain(m.ctx), /different main Session identity/);
  assert.notEqual((await mapping(paths)).sessionId, 'session-unrelated');
  assert.equal((await mapping(paths)).state, 'reserved');
});

test('deleted or unreadable ready Session never gets a replacement', async t => {
  const paths = await fixture(t);
  await seed(paths);
  const m = mock(paths);
  await assert.rejects(initializeParentMain(m.ctx), /refusing replacement/);
  assert.equal(m.creates.length, 0);
});

for (const meta of [{ cwd: '/other' }, { isSeeded: true }, { parentSession: 'session-other' }, { origin: { kind: 'external' } }]) {
  test(`refuses mismatched immutable Session metadata ${JSON.stringify(meta)}`, async t => {
    const paths = await fixture(t);
    const value = await seed(paths);
    const m = mock(paths, new Map([[value.sessionId, { meta: { id: value.sessionId, cwd: paths.workspace, isSeeded: false, ...meta }, events: [] }]]));
    await assert.rejects(initializeParentMain(m.ctx), /inspection does not match/);
    assert.equal(m.creates.length, 0);
    assert.equal(m.calls.some(([kind]) => kind === 'attach'), false);
  });
}

test('workspace attachment failure is retryable without creating another Session', async t => {
  const paths = await fixture(t);
  const m = mock(paths);
  m.ctx.workspaceRegistry.create = async () => ({ sessionIds: [], async attachSession() { throw new Error('attach failed'); } });
  await assert.rejects(initializeParentMain(m.ctx), /attach failed/);
  const restart = mock(paths, m.durable);
  const bot = await initializeParentMain(restart.ctx);
  assert.equal(bot.mainSessionId, (await mapping(paths)).sessionId);
  assert.equal(restart.creates.length, 0);
});

test('only the read-only inventory route is registered, unavailable until appReady completes durable attachment', async t => {
  const paths = await fixture(t);
  const m = mock(paths);
  await apply(m.ctx);
  assert.deepEqual([...m.routes.keys()], [PARENT_MAIN_PATH]);
  const route = m.routes.get(PARENT_MAIN_PATH);
  assert.deepEqual(route.methods, ['GET']);
  let response = await route.fetch();
  assert.equal(response.status, 503);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  await missing(paths.workspace);
  assert.equal(m.creates.length, 0);
  await m.start();
  response = await route.fetch();
  assert.equal(response.status, 200);
  const value = await response.json();
  assert.deepEqual(Object.keys(value).sort(), ['bot', 'mode']);
  assert.equal(value.mode, 'parent');
  assert.deepEqual(Object.keys(value.bot).sort(), ['handle', 'kind', 'mainSessionId', 'preview', 'status', 'workspace']);
  assert.equal(value.bot.mainSessionId, (await mapping(paths)).sessionId);
  const count = m.calls.length;
  await route.fetch();
  assert.equal(m.calls.length, count);
  m.dispose();
  assert.equal(m.ready.size, 0);
  assert.equal((await route.fetch()).status, 503);
});

test('optional parent main service publishes only the exact attached startup identity', async t => {
  const paths = await fixture(t);
  const m = mock(paths);
  assert.equal(m.ctx.get('magiMainSession'), undefined);
  await apply(m.ctx);
  const service = m.ctx.get('magiMainSession');
  assert.equal(service.current(), undefined);
  let entered, release;
  const attaching = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  const createWorkspace = m.ctx.workspaceRegistry.create;
  m.ctx.workspaceRegistry.create = async (...args) => {
    const project = await createWorkspace(...args);
    const attach = project.attachSession;
    return { ...project, async attachSession(id) { entered(); await gate; await attach.call(project, id); } };
  };
  const starting = m.start();
  await attaching;
  assert.equal(service.current(), undefined);
  release();
  await starting;
  const current = service.current();
  const bot = (await (await m.routes.get(PARENT_MAIN_PATH).fetch()).json()).bot;
  assert.deepEqual(current, { sessionId: bot.mainSessionId, workspace: bot.workspace });
  assert.equal(Object.isFrozen(current), true);
  assert.throws(() => { current.sessionId = 'other'; }, TypeError);
  await writeFile(paths.marker, '{unreadable mapping');
  const calls = m.calls.length;
  assert.equal(service.current(), current);
  assert.equal(m.calls.length, calls);
  m.dispose();
  assert.equal(service.current(), undefined);
  assert.equal(m.ctx.get('magiMainSession'), undefined);
});

test('parent inventory previews only fixed main, refreshes text, and releases native observations', async t => {
  const paths = await fixture(t);
  const m = mock(paths);
  let text = 'first answer', disposed = 0, cursor = 0;
  const ids = [];
  m.ctx.sessionQuery = { async observeSession(id) {
    ids.push(id);
    return { cursor, events: [{ seq: cursor, type: 'assistant/message', surfaceOp: 'append', data: { message: { content: [{ type: 'text', text }] } } }],
      [Symbol.dispose]() { disposed++; } };
  } };
  await apply(m.ctx); await m.start();
  const route = m.routes.get(PARENT_MAIN_PATH);
  assert.deepEqual((await (await route.fetch()).json()).bot.preview, { state: 'message', text: 'first answer' });
  text = 'new answer'; cursor++;
  assert.deepEqual((await (await route.fetch()).json()).bot.preview, { state: 'message', text: 'new answer' });
  assert.deepEqual(ids, [(await mapping(paths)).sessionId, (await mapping(paths)).sessionId]);
  assert.equal(disposed, 2);
  m.dispose();
});

test('startup errors are sanitized 503 and GET never retries a mutating initializer', async t => {
  const paths = await fixture(t);
  const m = mock(paths);
  let attempts = 0;
  m.ctx.sessionController.create = async () => { attempts++; throw new Error('secret-token /private/log'); };
  await apply(m.ctx);
  await m.start();
  assert.equal(m.ctx.get('magiMainSession').current(), undefined);
  const route = m.routes.get(PARENT_MAIN_PATH);
  for (let i = 0; i < 3; i++) {
    const response = await route.fetch();
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { code: 'parent-main-unavailable', error: 'The parent Bot is not ready.' });
  }
  assert.equal(attempts, 1);
});

test('disposal before appReady cannot initialize and in-flight completion cannot publish readiness', async t => {
  const paths = await fixture(t);
  const before = mock(paths);
  await apply(before.ctx);
  before.dispose();
  await before.start();
  assert.equal(before.creates.length, 0);
  await missing(paths.workspace);
  const during = mock(paths);
  let release;
  let entered;
  const enteredPromise = new Promise(resolve => { entered = resolve; });
  const flush = during.ctx.sessionPersistence.flush;
  during.ctx.sessionPersistence.flush = async () => { entered(); await new Promise(resolve => { release = resolve; }); await flush(); };
  await apply(during.ctx);
  const route = during.routes.get(PARENT_MAIN_PATH);
  const service = during.ctx.get('magiMainSession');
  assert.equal(service.current(), undefined);
  const starting = during.start();
  await enteredPromise;
  during.dispose();
  release();
  await starting;
  assert.equal(service.current(), undefined);
  assert.equal(during.ctx.get('magiMainSession'), undefined);
  assert.equal((await route.fetch()).status, 503);
});

test('EVA child homes contribute no route, no readiness callback, no workspace or Session', async t => {
  const paths = await fixture(t);
  await mkdir(join(paths.home, 'eva'));
  await writeFile(join(paths.home, 'eva', 'node.json'), JSON.stringify({ role: 'eva-child' }));
  const empty = mock(paths);
  await apply(empty.ctx);
  assert.equal(empty.ctx.get('magiMainSession'), undefined);
  assert.equal(empty.routes.size, 0);
  assert.equal(empty.ready.size, 0);
  assert.equal(empty.calls.length, 0);
  const m = mock(paths);
  const childService = { current: () => Object.freeze({ sessionId: 'child-owned', workspace: paths.home }) };
  m.ctx.provide('magiMainSession', childService);
  await apply(m.ctx);
  assert.equal(m.ctx.get('magiMainSession'), childService);
  assert.equal(await initializeParentMain(m.ctx), undefined);
  assert.equal(m.routes.size, 0);
  assert.equal(m.ready.size, 0);
  assert.equal(m.calls.length, 0);
  await missing(paths.workspace);
  await missing(paths.stateDir);
});

for (const target of ['home', 'profile', 'workspace', 'stateDir']) {
  test(`path policy rejects symlinked ${target} without Session mutation`, async t => {
    const paths = await fixture(t);
    const outside = join(paths.base, 'outside');
    await mkdir(outside);
    if (target === 'home' || target === 'profile') {
      const alias = join(paths.base, `alias-${target}`);
      await symlink(paths[target], alias);
      paths[target] = alias;
    } else await symlink(outside, paths[target]);
    const m = mock(paths);
    await assert.rejects(initializeParentMain(m.ctx), /Unsafe|symlink/);
    assert.equal(m.creates.length, 0);
  });
}

test('relative/noncanonical home and malformed role marker fail closed with sanitized API', async t => {
  const paths = await fixture(t);
  const m = mock(paths);
  m.ctx.profileContext.home = './not-an-absolute-home';
  await assert.rejects(initializeParentMain(m.ctx), /canonical absolute/);
  await apply(m.ctx);
  assert.equal((await m.routes.get(PARENT_MAIN_PATH).fetch()).status, 503);
  assert.equal(m.ctx.get('magiMainSession').current(), undefined);
  assert.equal(m.ready.size, 0);
  await mkdir(join(paths.home, 'eva'));
  await writeFile(join(paths.home, 'eva', 'node.json'), '{broken');
  m.ctx.profileContext.home = paths.home;
  await assert.rejects(initializeParentMain(m.ctx));
  assert.equal(m.creates.length, 0);
  await missing(paths.workspace);
});

test('existing non-private state directory is rejected rather than chmodded', async t => {
  const paths = await fixture(t);
  await mkdir(paths.stateDir, { mode: 0o755 });
  await chmod(paths.stateDir, 0o755);
  const m = mock(paths);
  await assert.rejects(initializeParentMain(m.ctx), /Unsafe/);
  assert.equal((await lstat(paths.stateDir)).mode & 0o077, 0o055);
  assert.equal(m.creates.length, 0);
});

for (const unsafe of ['symlink', 'hardlink', 'public', 'corrupt', 'oversized', 'legacy', 'wrong-workspace']) {
  test(`unsafe ${unsafe} reservation never creates or adopts a Session`, async t => {
    const paths = await fixture(t);
    if (unsafe === 'legacy') await seed(paths, { version: 1 });
    else if (unsafe === 'wrong-workspace') await seed(paths, { originalWorkspace: join(paths.workspace, 'workspace') });
    else {
      await mkdir(paths.workspace);
      await mkdir(paths.stateDir, { mode: 0o700 });
      const outside = join(paths.base, 'outside.json');
      await writeFile(outside, '{}', { mode: 0o600 });
      if (unsafe === 'symlink') await symlink(outside, paths.marker);
      else if (unsafe === 'hardlink') await link(outside, paths.marker);
      else await writeFile(paths.marker, unsafe === 'oversized' ? ' '.repeat(5000) : '{corrupt', { mode: 0o600 });
      if (unsafe === 'public') await chmod(paths.marker, 0o644);
    }
    const m = mock(paths);
    await assert.rejects(initializeParentMain(m.ctx));
    assert.equal(m.creates.length, 0);
    assert.equal(m.calls.some(([kind]) => kind === 'attach'), false);
  });
}

// Upgrade compatibility: this is the old companion's on-disk identity, not a
// Session created by the new package first. State namespace must not migrate.
test('consolidation reuses the prior companion identity and history without create or a new state directory', async t => {
  const paths = await fixture(t);
  const oldMapping = await seed(paths);
  const existing = { meta: { id: oldMapping.sessionId, cwd: paths.workspace, isSeeded: false }, events: [{ text: 'history from eva-main-bot' }] };
  const m = mock(paths, new Map([[oldMapping.sessionId, existing]]));
  const before = await readFile(paths.marker, 'utf8');
  await apply(m.ctx);
  await m.start();
  const response = await m.routes.get('/api/eva/parent-main').fetch();
  assert.equal(response.status, 200);
  assert.equal((await response.json()).bot.mainSessionId, oldMapping.sessionId);
  assert.equal(m.creates.length, 0);
  assert.deepEqual(m.stored.get(oldMapping.sessionId), existing);
  assert.equal(await readFile(paths.marker, 'utf8'), before);
  assert.equal((await mapping(paths)).profile, 'parent-main');
  assert.deepEqual(m.projects.get(paths.workspace).sessionIds, [oldMapping.sessionId]);
  await missing(join(paths.profile, 'magi-shell'));
  await missing(join(paths.profile, 'magi-shell-main-frame'));
});
