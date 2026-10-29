import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { ensureMainSession, attachMainSessionWorkspace, ensureMainSessionTitle } from '../main-session.js';
import { apply as applyBotMode } from '@magi/bot-mode';
import { registerChildView, registerEmbeddedLogin } from '../view.js';
import { createMessagePreviewReader } from '../message-preview.js';

async function fixture(t) {
  const root = await realpath(tmpdir());
  const home = await mkdtemp(join(root, 'eva-child-workspace-test-'));
  const profileDir = join(home, 'profiles', 'eva-000');
  await mkdir(profileDir, { recursive: true });
  await mkdir(join(home, 'eva'), { mode: 0o700 });
  t.after(async () => {
    assert.equal(resolve(home), home);
    assert.ok(home.startsWith(join(root, 'eva-child-workspace-test-')));
    await rm(home, { recursive: true });
  });
  const stored = new Map(), projects = new Map(), calls = [], routes = [], errors = [], messages = [], cleanups = [], timers = new Set();
  const services = new Map();
  const dispose = () => { for (const cleanup of cleanups.splice(0).reverse()) cleanup(); };
  t.after(dispose);
  let onReady, finish, attachGate, attachmentStarted, rejectAttach = false;
  const result = new Promise((done) => { finish = done; });
  const attaching = new Promise((done) => { attachmentStarted = done; });
  const options = { workspace: home, profile: 'eva-000', stateDir: join(home, 'eva') };
  const ctx = {
    get(name) { return services.get(name); },
    provide(name, value) {
      assert.ok(!services.has(name), `duplicate service: ${name}`);
      services.set(name, value);
      const cleanup = () => services.delete(name);
      cleanups.push(cleanup);
      return cleanup;
    },
    effect(callback) { const cleanup = callback(); if (cleanup) cleanups.push(cleanup); },
    appReady: { onReady(callback) { onReady = callback; return () => {}; } },
    appExit(code) { finish({ exit: code }); },
    profileContext: { home, name: 'eva-000', dir: profileDir },
    systemPrompt: { section() { return () => {}; } },
    agentPresets: { async resolve() { return { id: 'eva' }; } },
    llm: {
      listProviders() { calls.push(['provider-check']); return [{ id: 'openai-codex', name: 'OpenAI Codex' }]; },
      async listModels(provider) { assert.equal(provider, 'openai-codex'); return [{ id: 'synthetic-model' }]; },
    },
    webServer: { getOrigin() { return 'http://127.0.0.1:12345'; }, register() { return () => {}; } },
    connection: {
      authenticatedUrl(origin) { return origin; },
      fetch: { register(route) { routes.push(route); } },
    },
    sessionController: {
      async create({ cwd, sessionId }) {
        calls.push(['create', sessionId]);
        assert.ok(!stored.has(sessionId));
        stored.set(sessionId, { meta: { id: sessionId, cwd, isSeeded: false }, events: [] });
        return { sessionId };
      },
      async inspect(id) { assert.ok(stored.has(id)); return structuredClone(stored.get(id)); },
      async resolveAgent(id) { return { agent: { id } }; },
      async rename({ sessionId, title }) {
        calls.push(['rename', sessionId, title]);
        const events = stored.get(sessionId).events;
        events.push({ type: 'session/title', data: { title, messageSeqs: [], source: { kind: 'user' } } });
        return { title, seq: events.length - 1 };
      },
    },
    sessionQuery: { async readTitle(id) {
      return structuredClone(stored.get(id).events.findLast((event) => event.type === 'session/title')?.data);
    } },
    workingDirectory: { async set(agent, path) { stored.get(agent.id).effectiveCwd = path; return path; } },
    sessionPersistence: { async flush() { calls.push(['flush']); } },
    workspaceRegistry: { async create(path) {
      const canonical = await realpath(path);
      if (!projects.has(canonical)) projects.set(canonical, {
        id: `workspace-${projects.size}`, path: canonical, sessionIds: [],
        async attachSession(id) {
          calls.push(['attach', id, canonical]);
          attachmentStarted();
          assert.equal(stored.get(id).meta.cwd, canonical, 'native membership validates immutable header cwd');
          if (attachGate) await attachGate;
          if (rejectAttach) throw new Error('membership write failed');
          if (!this.sessionIds.includes(id)) this.sessionIds.push(id);
        },
      });
      return projects.get(canonical);
    } },
  };
  return {
    home, ctx, options, stored, projects, calls, routes, errors, result, attaching, messages, dispose,
    poll() { for (const callback of timers) callback(); },
    holdAttachment() { let release; attachGate = new Promise((done) => { release = done; }); return release; },
    rejectAttachment() { rejectAttach = true; },
    async start(sharing = {}, resolvedStore = sharing.credentialsPath) {
      await applyBotMode(ctx, { role: 'eva-child', workspace: home, profile: 'eva-000' });
      ctx.magiBotMode = ctx.get('magiBotMode');
      // Run the actual bootstrap body with only its platform boundary injected.
      // This is not a browser/visual test; owner helpers and view routes are real.
      const source = (await readFile(new URL('../child.js', import.meta.url), 'utf8'))
        .replace(/^import .*;$/gm, '').replace(/^export /gm, '');
      runInNewContext(`${source}\napply(ctx, config);`, {
        ctx, config: { workspace: home, profile: 'eva-000', ...sharing }, realpath, join,
        credentialStorePath: async () => { calls.push(['credential-check']); return resolvedStore; },
        ensureMainSession, attachMainSessionWorkspace, ensureMainSessionTitle, registerChildView, registerEmbeddedLogin, createMessagePreviewReader, URL, AbortController,
        setInterval(callback) { timers.add(callback); return { callback, unref() {} }; }, clearInterval(timer) { timers.delete(timer.callback); },
        process: { cwd: () => home, connected: true, pid: 123, ppid: 12, send(message, callback) { messages.push(message); if (message.type === 'eva-ready') { calls.push(['ready']); finish({ message }); } callback?.(); } },
        console: { error(message) { errors.push(message); } },
      });
      onReady();
    },
    view() { return routes.find((route) => route.path === '/api/eva/view').fetch(); },
  };
}

test('child optional main service is exact, frozen, read-only and unavailable after unload', async (t) => {
  const f = await fixture(t);
  assert.equal(f.ctx.get('magiMainSession'), undefined);
  await f.start();
  const service = f.ctx.get('magiMainSession');
  assert.equal(service.current(), undefined);
  const { message } = await f.result;
  const current = service.current();
  assert.deepEqual({ ...current }, { sessionId: message.mainSessionId, workspace: f.home });
  assert.equal(Object.isFrozen(current), true);
  assert.throws(() => { current.sessionId = 'other'; }, TypeError);
  await writeFile(join(f.options.stateDir, 'main-session.json'), '{unreadable mapping');
  const calls = f.calls.length;
  assert.equal(service.current(), current);
  assert.equal(f.calls.length, calls);
  f.dispose();
  assert.equal(service.current(), undefined);
  assert.equal(f.ctx.get('magiMainSession'), undefined);
});

test('child disposal during attachment never publishes a late main service', async (t) => {
  const f = await fixture(t);
  const release = f.holdAttachment();
  let finish, flushes = 0;
  const finished = new Promise(resolve => { finish = resolve; });
  f.ctx.sessionPersistence.flush = async () => { if (++flushes === 2) finish(); };
  await f.start();
  const service = f.ctx.get('magiMainSession');
  await f.attaching;
  f.dispose();
  release();
  await finished;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(service.current(), undefined);
  assert.equal(f.ctx.get('magiMainSession'), undefined);
  assert.ok(!f.calls.some(([kind]) => kind === 'ready'));
});

test('child preview polling is fixed-main, non-overlapping and stops late sends on disposal', async (t) => {
  const f = await fixture(t);
  await f.start();
  const { message } = await f.result;
  let resolveObservation, reads = 0, released = 0, signal;
  f.ctx.sessionQuery.observeSession = async (id, options) => {
    reads++; signal = options.signal;
    assert.equal(id, message.mainSessionId);
    return new Promise(resolve => { resolveObservation = resolve; });
  };
  f.poll(); f.poll();
  assert.equal(reads, 1);
  resolveObservation({ cursor: 0, events: [{ seq: 0, type: 'user/message', surfaceOp: 'append', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'child question' }] } }], [Symbol.dispose]() { released++; } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.messages.at(-1).type, 'eva-preview');
  assert.equal(f.messages.at(-1).preview.text, 'child question');
  assert.equal(released, 1);
  f.poll();
  assert.equal(reads, 2);
  f.dispose();
  assert.equal(signal.aborted, true);
  const count = f.messages.length;
  resolveObservation({ cursor: 1, events: [], [Symbol.dispose]() { released++; } });
  await new Promise(resolve => setImmediate(resolve));
  f.poll();
  assert.equal(reads, 2);
  assert.equal(f.messages.length, count);
  assert.equal(released, 2);
});

test('child attaches its blank fixed Session before exposing view/readiness', async (t) => {
  const f = await fixture(t);
  const release = f.holdAttachment();
  await f.start();
  await f.attaching;
  assert.equal(f.ctx.get('magiMainSession').current(), undefined);
  assert.equal((await f.view()).status, 503);
  assert.ok(!f.calls.some(([kind]) => kind === 'ready'));
  release();
  const { message } = await f.result;
  assert.ok(f.projects.get(f.home).sessionIds.includes(message.mainSessionId));
  assert.equal(message.defaultAgentPreset, 'eva');
  assert.equal((await (await f.view()).json()).mainSessionId, message.mainSessionId);
  assert.equal(f.calls.filter(([kind]) => kind === 'create').length, 1);
  assert.equal(f.calls.at(-1)[0], 'ready');
});

test('existing unregistered main is repaired without adopting or deleting other conversations', async (t) => {
  const f = await fixture(t);
  const main = await ensureMainSession(f.ctx, f.options);
  const other = { meta: { id: 'session-other', cwd: f.home, isSeeded: false }, events: [{ text: 'existing conversation' }] };
  f.stored.set(other.meta.id, structuredClone(other));
  const workspace = await f.ctx.workspaceRegistry.create(f.home);
  await workspace.attachSession(other.meta.id);
  f.calls.length = 0;
  await f.start();
  assert.equal((await f.result).message.mainSessionId, main.sessionId);
  assert.deepEqual(f.stored.get(other.meta.id), other);
  assert.deepEqual(new Set(workspace.sessionIds), new Set([main.sessionId, other.meta.id]));
  assert.equal(f.calls.filter(([kind]) => kind === 'create').length, 0);
  // Reopening an attached blank resolves the native Hero's Workspace chip;
  // there is no need to pick a Workspace and trigger openWorkspace/create.
  assert.ok([...f.projects.values()].find((entry) => entry.sessionIds.includes(main.sessionId)));
});

test('legacy child upgrades effective cwd but attaches to its immutable Workspace owner', async (t) => {
  const f = await fixture(t);
  const original = join(f.home, 'workspace');
  await mkdir(original);
  const main = await ensureMainSession(f.ctx, { ...f.options, workspace: original });
  const marker = join(f.options.stateDir, 'main-session.json');
  const mapping = JSON.parse(await readFile(marker, 'utf8'));
  delete mapping.originalWorkspace;
  await writeFile(marker, JSON.stringify({ ...mapping, version: 1 }));
  f.stored.get(main.sessionId).events.push({ text: 'historical conversation' });
  const history = structuredClone(f.stored.get(main.sessionId).events);
  f.calls.length = 0;
  await f.start();
  assert.equal((await f.result).message.mainSessionId, main.sessionId);
  assert.ok(f.projects.get(original).sessionIds.includes(main.sessionId));
  assert.ok(!f.projects.get(f.home).sessionIds.includes(main.sessionId));
  assert.equal(f.stored.get(main.sessionId).meta.cwd, original);
  assert.equal(f.stored.get(main.sessionId).effectiveCwd, f.home);
  assert.deepEqual(f.stored.get(main.sessionId).events.slice(0, history.length), history);
  assert.equal(f.stored.get(main.sessionId).events.length, history.length + 1);
  assert.deepEqual(await f.ctx.sessionQuery.readTitle(main.sessionId), {
    title: 'EVA-000', messageSeqs: [], source: { kind: 'user' },
  });
  const upgraded = JSON.parse(await readFile(marker, 'utf8'));
  assert.equal(upgraded.version, 2);
  assert.equal(upgraded.workspace, f.home);
  assert.equal(upgraded.originalWorkspace, original);
  assert.equal(f.calls.filter(([kind]) => kind === 'create').length, 0);
});

test('failed membership blocks readiness and never substitutes a Session', async (t) => {
  const f = await fixture(t);
  const main = await ensureMainSession(f.ctx, f.options);
  f.calls.length = 0;
  f.rejectAttachment();
  await f.start();
  assert.equal((await f.result).exit, 1);
  assert.equal(f.ctx.get('magiMainSession').current(), undefined);
  assert.equal((await f.view()).status, 503);
  assert.ok(!f.calls.some(([kind]) => kind === 'ready' || kind === 'create'));
  assert.ok(f.stored.has(main.sessionId));
  assert.match(f.errors[0], /membership write failed/);
});

test('child replaces only the fixed main generated title and waits for its durable pin', async (t) => {
  const f = await fixture(t);
  const main = await ensureMainSession(f.ctx, f.options);
  const oldTitle = { type: 'session/title', data: { title: 'Simple addition question', source: { kind: 'provider' } } };
  f.stored.get(main.sessionId).events.push(oldTitle);
  const other = { meta: { id: 'session-other', cwd: f.home }, events: [structuredClone(oldTitle)] };
  f.stored.set(other.meta.id, structuredClone(other));
  let release, started, flushes = 0;
  const barrier = new Promise((done) => { release = done; });
  const flushing = new Promise((done) => { started = done; });
  f.ctx.sessionPersistence.flush = async () => {
    if (++flushes === 2) { started(); await barrier; }
  };
  await f.start();
  await flushing;
  assert.equal(f.ctx.get('magiMainSession').current(), undefined);
  assert.equal((await f.view()).status, 503);
  assert.ok(!f.calls.some(([kind]) => kind === 'ready'));
  release();
  assert.equal((await f.result).message.mainSessionId, main.sessionId);
  const title = await f.ctx.sessionQuery.readTitle(main.sessionId);
  assert.equal(title.title, 'EVA-000');
  assert.equal(title.source.kind, 'user');
  assert.deepEqual(f.stored.get(main.sessionId).events[0], oldTitle);
  assert.deepEqual(f.stored.get(other.meta.id), other);
  const before = structuredClone(f.stored.get(main.sessionId));
  await ensureMainSessionTitle(f.ctx, main.sessionId, f.options.profile);
  assert.deepEqual(f.stored.get(main.sessionId), before);
});

test('failed main title initialization blocks readiness without replacing the fixed Session', async (t) => {
  const f = await fixture(t);
  const main = await ensureMainSession(f.ctx, f.options);
  f.calls.length = 0;
  f.ctx.sessionController.rename = async () => { throw new Error('title write failed'); };
  await f.start();
  assert.equal((await f.result).exit, 1);
  assert.equal(f.ctx.get('magiMainSession').current(), undefined);
  assert.equal((await f.view()).status, 503);
  assert.ok(!f.calls.some(([kind]) => kind === 'ready' || kind === 'create'));
  assert.ok(f.stored.has(main.sessionId));
  assert.match(f.errors[0], /title write failed/);
});

test('shared child verifies its provider before creating the main Session and reports no store path', async (t) => {
  const f = await fixture(t);
  await f.start({ credentialsPath: '/synthetic/shared.yaml' });
  const { message } = await f.result;
  assert.equal(message.credentialsShared, true);
  assert.equal(message.codexAvailable, true);
  assert.ok(f.calls.findIndex(([kind]) => kind === 'provider-check') < f.calls.findIndex(([kind]) => kind === 'create'));
  assert.ok(f.calls.findIndex(([kind]) => kind === 'credential-check') < f.calls.findIndex(([kind]) => kind === 'create'));
  assert.ok(!JSON.stringify(message).includes('/synthetic/shared.yaml'));
});

for (const missing of ['route', 'models']) test(`missing native Codex ${missing} blocks readiness before any Session mutation`, async (t) => {
  const f = await fixture(t);
  if (missing === 'route') f.ctx.llm.listProviders = () => [{ id: 'deepseek-official', name: 'DeepSeek' }];
  else f.ctx.llm.listModels = async () => [];
  await f.start();
  assert.equal((await f.result).exit, 1);
  assert.equal(f.stored.size, 0);
  assert.equal((await f.view()).status, 503);
  assert.match(f.errors[0], /native Codex provider is unavailable/);
  assert.ok(!f.calls.some(([kind]) => kind === 'ready' || kind === 'create'));
});

test('mismatched child credential provider blocks readiness before any Session mutation', async (t) => {
  const f = await fixture(t);
  await f.start({ credentialsPath: '/synthetic/parent.yaml' }, '/synthetic/child.yaml');
  assert.equal((await f.result).exit, 1);
  assert.equal(f.stored.size, 0);
  assert.equal((await f.view()).status, 503);
  assert.ok(!f.calls.some(([kind]) => kind === 'ready'));
});
