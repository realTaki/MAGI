import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function fixture({ embedded = true, hash = '#eva-main', failure, initiallyReady = false } = {}) {
  let plugin, mounted = false, retained = false;
  const opened = [], entries = [], cleanup = [], effects = [], states = [], replacements = [], messages = [];
  const listeners = new Map(), paints = new Map();
  let phaseOverride, offReport, paintId = 0;
  function source(snapshot) {
    const listeners = new Set();
    return { snapshot, listeners, getSnapshot() { return this.snapshot; }, subscribe(callback) { listeners.add(callback); return () => listeners.delete(callback); }, update(patch) { Object.assign(this.snapshot, patch); for (const callback of [...listeners]) callback(); } };
  }
  const sessionList = source({ phase: initiallyReady ? 'ready' : 'pending', byId: failure === 'missing' ? {} : { 'fixed-main': { sessionId: 'fixed-main' } } });
  const workspaceList = source({ phase: initiallyReady ? 'ready' : 'pending' });
  const session = source({ openState: 'opening', removed: false });
  // Inspect declarations and effect lifecycle only, not DOM/visual rendering.
  const React = {
    Fragment: Symbol('Fragment'),
    createElement(type, props, ...children) { return { type, props, children }; },
    useState(value) { return [value === 'opening' ? phaseOverride ?? value : value, (next) => states.push(next)]; },
    useEffect(callback) { effects.push(callback); },
  };
  const window = {
    __ModuleLoader__: { load({ factory }) { plugin = factory(() => React); } },
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(callback); },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
  };
  window.parent = embedded ? { postMessage(message, origin) { messages.push({ message, origin }); } } : window;
  const historyState = { native: 'preserved' };
  runInNewContext(await readFile(new URL('../client.js', import.meta.url), 'utf8'), {
    window, location: { hash, pathname: '/', search: '' },
    history: { state: historyState, replaceState(...args) { replacements.push(args); } }, AbortController, queueMicrotask,
    requestAnimationFrame(callback) { const id = ++paintId; paints.set(id, callback); return id; },
    cancelAnimationFrame(id) { paints.delete(id); },
    globalThis: { __DSH_TRANSPORT__: { fetch: async () => ({ ok: true, json: async () => ({ mode: 'child', mainSessionId: 'fixed-main' }) }) } },
    setTimeout() { assert.fail('Child must not poll parent EVA entries'); }, clearTimeout() {},
  });
  plugin.apply({
    effect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
    locale: { register() { return () => {}; }, bind() { return (key) => key; } },
    slots: {
      inject(name, callback) { assert.equal(name, 'shell.overlay', 'keep the official sidebar and its descendant seats mounted'); return callback(); },
      register(options, component) {
        const entry = { options, component, disposed: false };
        entries.push(entry);
        return () => { entry.disposed = true; };
      },
    },
    workspaces: { list: workspaceList },
    sessions: {
      list: sessionList,
      retainInfo(id) { assert.equal(id, 'fixed-main'); return { getSnapshot() { return { retainedBy: { mainView: retained ? 1 : 0 } }; } }; },
      binding(id) { assert.equal(id, 'fixed-main'); return failure === 'binding' ? undefined : { session }; },
      create() { assert.fail('Never replace the fixed Session by creating a new one'); },
    },
    uiWorkspace: { openSession(id) {
      assert.equal(mounted, true, 'navigation requires the live frame');
      assert.equal(workspaceList.snapshot.phase, 'ready');
      assert.equal(sessionList.snapshot.phase, 'ready');
      opened.push(id);
      if (failure === 'throw') throw new Error('Native retain refused');
      retained = failure !== 'retention';
    } },
  });
  await tick();
  return {
    opened, entries, states, replacements, sessionList, workspaceList, session, historyState, messages, paints, listeners,
    mount() {
      mounted = true;
      const declaration = entries[0].component();
      effects.splice(0).forEach((effect, index) => {
        const dispose = effect();
        if (index === 0) offReport = dispose;
        if (dispose) cleanup.push(dispose);
      });
      return declaration;
    },
    // Exercise just the reporting effect's dependency change, not a DOM renderer.
    reportPhase(phase) {
      offReport?.(); phaseOverride = phase; entries[0].component();
      offReport = effects.splice(0)[0]();
      if (offReport) cleanup.push(offReport);
    },
    paint() { const pending = [...paints.values()]; paints.clear(); for (const callback of pending) callback(); },
    request(sourceIsParent = true) {
      for (const callback of listeners.get('message') ?? []) callback({ source: sourceIsParent ? window.parent : {}, data: { type: 'eva:main-state-request', sessionId: 'fixed-main' } });
    },
    pagehide() { for (const callback of listeners.get('pagehide') ?? []) callback(); },
    dispose() { for (const dispose of cleanup.splice(0).reverse()) dispose(); },
  };
}

for (const embedded of [true, false]) for (const hash of ['', '#eva-main']) {
  test(`child embedded=${embedded}, hash=${hash || '(cleared after login)'}`, async () => {
    const f = await fixture({ embedded, hash });
    assert.deepEqual(f.opened, [], 'do not navigate before the frame mounts');
    assert.equal(f.entries.length, embedded || hash ? 1 : 0);
    if (!f.entries.length) { f.dispose(); return; }
    assert.equal(f.entries[0].options.name, 'shell.overlay');
    assert.equal(f.entries[0].options.id, 'eva:embedded-frame');
    const declaration = f.mount();
    const marker = declaration.children[0];
    if (embedded) {
      assert.equal(marker.props['data-eva-embedded-frame'], '');
      assert.equal(marker.props.hidden, true);
      const css = marker.children[0].children[0];
      assert.match(css, /:has\(> \[data-rightbar-col\]\):has\(\[data-eva-embedded-frame\]\) > div:first-of-type \{ display: none; \}/);
      assert.match(css, /> div:has\(\+ \[data-rightbar-col\]\) \{ grid-column: 1 \/ 3; grid-row: 1; \}/);
      assert.match(css, /\[data-side="sidebar"\]/);
      assert.match(css, /\[data-shell-leading\]/);
      assert.doesNotMatch(css, /\[data-side="rightbar"\]|grid-template-columns|querySelector|className/);
    } else assert.equal(marker, false, 'standalone child keeps the full layout');
    f.sessionList.update({ phase: 'ready' });
    await tick();
    assert.deepEqual(f.opened, [], 'Session list alone is insufficient');
    f.workspaceList.update({ phase: 'ready' });
    await tick();
    assert.deepEqual(f.opened, ['fixed-main']);
    assert.ok(!f.states.includes('ready'), 'retention is not proof of history opening');
    assert.equal(f.replacements.length, 0, 'keep the navigation marker until success');
    f.session.update({ openState: 'open' });
    assert.equal(f.states.at(-1), 'ready');
    assert.equal(f.replacements.length, hash ? 1 : 0);
    if (hash) assert.equal(f.replacements[0][0], f.historyState, 'preserve native history state');
    f.sessionList.update({ phase: 'ready' });
    await tick();
    assert.equal(f.opened.length, 1, 'do not repeatedly force selection');
    f.dispose();
    assert.equal(f.entries[0].disposed, true);
    for (const source of [f.sessionList, f.workspaceList, f.session]) assert.equal(source.listeners.size, 0);
  });
}

for (const failure of ['throw', 'binding', 'retention', 'history', 'removed']) {
  test(`fixed Session activation reports ${failure} without accepting a new conversation`, async () => {
    const f = await fixture({ failure, initiallyReady: true });
    f.mount();
    await tick();
    if (failure === 'history') f.session.update({ openState: 'error' });
    if (failure === 'removed') f.session.update({ removed: true });
    assert.equal(f.states.at(-1), 'failed');
    assert.ok(!f.states.includes('ready'));
    assert.equal(f.replacements.length, 0);
    assert.deepEqual(f.opened, ['fixed-main']);
    f.dispose();
  });
}

test('unmount cancels queued navigation and later history callbacks', async () => {
  const f = await fixture({ initiallyReady: true });
  f.mount();
  f.dispose();
  const states = f.states.length;
  await tick();
  f.session.update({ openState: 'open' });
  assert.deepEqual(f.opened, []);
  assert.equal(f.states.length, states);
  assert.equal(f.replacements.length, 0);
});

test('missing fixed Session fails explicitly instead of opening or creating another', async () => {
  const f = await fixture({ failure: 'missing', initiallyReady: true });
  f.mount();
  await tick();
  assert.equal(f.states.at(-1), 'failed');
  assert.deepEqual(f.opened, []);
  assert.equal(f.replacements.length, 0);
  f.dispose();
});

test('a target disappearing during history opening is not reported as success', async () => {
  const f = await fixture({ initiallyReady: true });
  f.mount();
  await tick();
  f.sessionList.update({ byId: {} });
  f.session.update({ openState: 'open' });
  assert.equal(f.states.at(-1), 'failed');
  assert.equal(f.replacements.length, 0);
  f.dispose();
});

test('child announces ready only after the ready commit gets a full paint opportunity', async () => {
  const f = await fixture({ initiallyReady: true });
  try {
    f.mount(); await tick();
    assert.equal(f.messages.at(-1).message.state, 'opening');
    f.session.update({ openState: 'open' });
    assert.equal(f.states.at(-1), 'ready');
    f.reportPhase('ready');
    f.request(); // A probe must not bypass the paint gate.
    assert.equal(f.messages.at(-1).message.state, 'opening');
    f.paint();
    assert.equal(f.messages.at(-1).message.state, 'opening');
    f.paint();
    assert.equal(f.messages.at(-1).message.state, 'ready');
    assert.equal(f.messages.at(-1).message.sessionId, 'fixed-main');
    assert.deepEqual(Object.keys(f.messages.at(-1).message).sort(), ['sessionId', 'state', 'type'], 'never send credentials or launch URLs');
    const count = f.messages.length;
    f.request(false); assert.equal(f.messages.length, count, 'ignore non-parent probes');
    f.request(); assert.equal(f.messages.length, count + 1, 'recover a missed readiness message');
    f.pagehide(); assert.equal(f.messages.at(-1).message.state, 'opening', 'cover native reload before its intermediate page');
  } finally { f.dispose(); }
  assert.equal(f.paints.size, 0);
  for (const callbacks of f.listeners.values()) assert.equal(callbacks.size, 0);
});

test('unmount cancels pending readiness paints and failure remains retryable', async () => {
  const f = await fixture();
  f.mount();
  f.reportPhase('ready'); f.paint();
  assert.equal(f.paints.size, 1);
  f.reportPhase('failed');
  assert.equal(f.paints.size, 0);
  assert.equal(f.messages.at(-1).message.state, 'failed', 'reveal the native error/retry overlay');
  f.reportPhase('ready'); f.paint();
  const count = f.messages.length;
  f.dispose(); f.paint(); f.request();
  assert.equal(f.messages.length, count);
  assert.equal(f.paints.size, 0);
});

test('standalone main navigation never sends framing messages or waits for paints', async () => {
  const f = await fixture({ embedded: false });
  try { f.mount(); f.reportPhase('ready'); assert.equal(f.messages.length, 0); assert.equal(f.paints.size, 0); }
  finally { f.dispose(); }
});
