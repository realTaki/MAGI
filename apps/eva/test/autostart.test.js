import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdtemp, mkdir, writeFile, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { restoreConfiguredNodes } from '../autostart.js';
import { EvaRuntime, validateName } from '../runtime.js';

const offline = handle => ({ handle, status: 'not-started' });
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

test('an empty inventory never allocates a default node', async () => {
  const calls = [];
  await restoreConfiguredNodes({ status: async (...args) => { calls.push(args); return { nodes: [] }; }, start() { assert.fail('must not create'); } });
  assert.deepEqual(calls, [[undefined]]);
});

test('restores fixed known handles once, skipping active, absent and malformed identities', async () => {
  const starts = [];
  const signal = new AbortController().signal;
  await restoreConfiguredNodes({
    status: async () => ({ nodes: [offline('eva-000'), offline('eva-000'),
      offline('eva-001'), offline('named-bot'),
      ...['starting', 'running', 'stopping', 'stopped', 'failed', 'unknown'].map(status => ({ handle: `bot-${status}`, status })),
      offline(undefined), offline(''), offline('../unsafe'), offline('BAD')] }),
    start: async (...args) => { starts.push(args); },
  }, { signal });
  assert.deepEqual(starts, [[undefined, 'eva-000', signal], [undefined, 'eva-001', signal], [undefined, 'named-bot', signal]]);
});

test('bounds startup to two workers and isolates individual failures without automatic retry', async () => {
  const gates = [deferred(), deferred(), deferred(), deferred()], starts = [], errors = [];
  let active = 0, maxActive = 0;
  const restoring = restoreConfiguredNodes({
    status: async () => ({ nodes: gates.map((_, i) => offline(`eva-00${i}`)) }),
    async start(_base, handle) {
      const i = starts.length; starts.push(handle); active++; maxActive = Math.max(active, maxActive);
      try { return await gates[i].promise; } finally { active--; }
    },
  }, { onError: (...args) => errors.push(args) });
  await tick(); assert.deepEqual(starts, ['eva-000', 'eva-001']);
  gates[0].reject(new Error('private-token-and-path')); await tick();
  assert.deepEqual(starts, ['eva-000', 'eva-001', 'eva-002'], 'one slow first wave does not hold every later node');
  gates[2].resolve(); await tick(); assert.equal(starts.length, 4);
  gates[3].resolve(); gates[1].resolve(); await restoring;
  assert.equal(maxActive, 2);
  assert.deepEqual(errors, [['eva-000']], 'raw errors never enter startup diagnostics');
  assert.equal(starts.filter(handle => handle === 'eva-000').length, 1);
});

test('inventory failure is non-fatal, sanitized and never causes default allocation', async () => {
  const errors = [];
  await restoreConfiguredNodes({ status: async () => { throw new Error('private-home-and-credentials'); }, start() { assert.fail('must not create'); } },
    { onError: (...args) => errors.push(args) });
  assert.deepEqual(errors, [[undefined]]);
});

test('already-aborted restore does not read inventory', async () => {
  const controller = new AbortController(); controller.abort();
  await restoreConfiguredNodes({ status() { assert.fail('disposed'); } }, { signal: controller.signal });
});

test('disposal while inventory is pending does not launch late nodes', async () => {
  const controller = new AbortController(), inventory = deferred();
  const restoring = restoreConfiguredNodes({ status: () => inventory.promise, start() { assert.fail('late start'); } }, { signal: controller.signal });
  controller.abort(); inventory.resolve({ nodes: [offline('eva-000')] });
  await restoring;
});

test('disposal aborts active startup and prevents queued starts or late failure warnings', async () => {
  const controller = new AbortController(), starts = [], errors = [];
  const restoring = restoreConfiguredNodes({
    status: async () => ({ nodes: [offline('eva-000'), offline('eva-001'), offline('eva-002')] }),
    start(_base, handle, signal) {
      assert.equal(signal, controller.signal); starts.push(handle);
      return new Promise((_, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
    },
  }, { signal: controller.signal, onError: value => errors.push(value) });
  await tick(); controller.abort(); await restoring;
  assert.deepEqual(starts, ['eva-000', 'eva-001']);
  assert.deepEqual(errors, []);
});

test('restoration consumes the real safe persisted inventory, not mere directory names', async (t) => {
  const root = await realpath(tmpdir());
  const home = await mkdtemp(join(root, 'eva-autostart-test-'));
  t.after(async () => {
    assert.equal(resolve(home), home); assert.ok(home.startsWith(join(root, 'eva-autostart-test-')));
    await rm(home, { recursive: true });
  });
  for (const handle of ['eva-000', 'old-bot', 'empty-bot', 'bad-bot']) await mkdir(join(home, `.dsh-${handle}`, 'eva'), { recursive: true });
  await writeFile(join(home, '.dsh-eva-000/eva/node.json'), JSON.stringify({ role: 'eva-child' }));
  await writeFile(join(home, '.dsh-old-bot/eva/main-session.json'), JSON.stringify({ version: 2, profile: 'old-bot', sessionId: 'session-original' }));
  await writeFile(join(home, '.dsh-bad-bot/eva/node.json'), JSON.stringify({ role: 'unrelated' }));
  const manager = new EvaRuntime({}, { homeRoot: home });
  const starts = [];
  manager.start = async (...args) => { starts.push(args); };
  await restoreConfiguredNodes(manager);
  assert.deepEqual(starts.map(args => args[1]), ['eva-000', 'old-bot']);
  assert.ok(starts.every(args => args[0] === undefined && args[1] !== undefined));
});

async function hostFixture({ child = false, restore = restoreConfiguredNodes } = {}) {
  const cleanups = [], calls = [], warnings = [];
  let ready, cancelled = false, tool, runtimeOptions;
  const manager = { status: async () => ({ nodes: [offline('eva-000')] }),
    homeRoot: '/parent', paths: handle => ({ home: `/bots/${handle}`, workspace: `/bots/${handle}` }),
    async start(...args) { calls.push(['start', ...args]); },
    async stop(...args) { calls.push(['stop', ...args]); }, dispose() { calls.push(['dispose']); } };
  const ctx = {
    profileContext: { home: '/parent', installAnchor: '/installed' },
    effect(callback) { const cleanup = callback(); if (cleanup) cleanups.push(cleanup); },
    appReady: { onReady(callback) { ready = callback; calls.push(['subscribe']); return () => { cancelled = true; calls.push(['cancel-ready']); }; } },
    tools: { register(value) { tool = value; calls.push(['tool']); } },
    workingDirectory: { async ensure() { return '/parent'; } },
    approval: { async request() { calls.push(['approval']); return 'denied'; } },
  };
  const source = (await readFile(new URL('../index.js', import.meta.url), 'utf8')).replace(/^import .*;$/gm, '').replace(/^export /gm, '');
  runInNewContext(`${source}\napply(ctx);`, {
    ctx, AbortController, validateName, defineTool: value => value,
    isEvaChild: () => child, desktopRuntime: () => ({}), credentialStorePath: async () => '/shared/store',
    EvaRuntime: function (_runtime, options) { calls.push(['manager']); runtimeOptions = options; return manager; },
    installParentDm(_ctx, installedManager, factory) {
      assert.equal(installedManager, manager); assert.equal(typeof factory, 'function');
      calls.push(['dm-install']);
      return { attach(channel, handle) { calls.push(['dm-attach', channel, handle]); return () => { calls.push(['dm-detach', handle]); }; } };
    },
    restoreConfiguredNodes: restore, registerParentView() { calls.push(['routes']); },
    console: { warn: value => warnings.push(value) },
  });
  return { manager, calls, warnings, tool, ctx, runtimeOptions, ready() { if (!cancelled) return ready?.(); },
    dispose() { for (const cleanup of cleanups.splice(0).reverse()) cleanup(); } };
}

test('parent activation wires runtime process-derived channels to the DM broker', async () => {
  const host = await hostFixture();
  try {
    assert.equal(host.calls.filter(([kind]) => kind === 'dm-install').length, 1);
    assert.equal(typeof host.runtimeOptions.dmAttach, 'function');
    const channel = {};
    const detach = host.runtimeOptions.dmAttach(channel, 'eva-000');
    const attached = host.calls.find(([kind]) => kind === 'dm-attach');
    assert.equal(attached[1], channel);
    assert.equal(attached[2], 'eva-000');
    assert.equal(typeof detach, 'function');
    detach();
    assert.ok(host.calls.some(([kind, handle]) => kind === 'dm-detach' && handle === 'eva-000'));
    assert.ok(!host.calls.some(([kind]) => kind === 'start'), 'installing DM does not auto-start a process');
  } finally { host.dispose(); }
});

test('Host activation defers restore until ready and does not await node startup', async () => {
  const gate = deferred();
  let options;
  const host = await hostFixture({ restore: async (manager, value) => { assert.ok(manager); options = value; await gate.promise; } });
  assert.equal(options, undefined, 'no start during activation');
  assert.equal(host.ready(), undefined, 'Host readiness must not await a child process');
  assert.equal(options.signal.aborted, false);
  host.dispose(); assert.equal(options.signal.aborted, true);
  gate.resolve(); await tick();
  assert.equal(host.calls.filter(([kind]) => kind === 'dispose').length, 1);
});

test('Host disposal before ready cancels automatic restoration', async () => {
  const host = await hostFixture(); host.dispose(); host.ready(); await tick();
  assert.ok(!host.calls.some(([kind]) => kind === 'start'));
  assert.ok(host.calls.some(([kind]) => kind === 'cancel-ready'));
});

test('ready parent restores without opening a browser and child Hosts expose no recursive launcher', async () => {
  const parent = await hostFixture(); parent.ready(); parent.ready(); await tick(); parent.ready(); await tick();
  const starts = parent.calls.filter(([kind]) => kind === 'start');
  assert.equal(starts.length, 1); assert.equal(starts[0][2], 'eva-000');
  parent.dispose();
  const child = await hostFixture({ child: true }); child.ready(); child.dispose();
  assert.deepEqual(child.calls, []);
});

test('automatic lifecycle restoration does not bypass manual tool approval', async () => {
  const host = await hostFixture();
  const exec = { agent: {}, signal: new AbortController().signal, callId: 'manual-call' };
  for (const action of ['start', 'stop']) {
    await assert.rejects(host.tool.execute({ action, handle: 'eva-000' }, exec), /was not approved/);
  }
  assert.equal(host.calls.filter(([kind]) => kind === 'approval').length, 2);
  assert.ok(!host.calls.some(([kind]) => kind === 'start' || kind === 'stop'));
  host.ctx.approval.request = async () => 'allowed-once';
  await host.tool.execute({ action: 'start', handle: 'eva-000' }, exec);
  assert.equal(host.calls.filter(([kind]) => kind === 'start').length, 1);
  host.dispose();
});

test('unexpected startup rejection is handled without leaking process details', async () => {
  const host = await hostFixture({ restore: async () => { throw new Error('secret-token'); } });
  host.ready(); await tick();
  assert.deepEqual(host.warnings, ['EVA automatic startup is unavailable; retry explicitly.']);
  host.dispose();
});
