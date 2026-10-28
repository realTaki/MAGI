import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

// Inspect component declarations and effect boundaries, not browser pixels.
async function fixture() {
  let plugin, framePhase = false, ready = false, collect = true;
  const active = new Map(), listeners = new Set(), timers = new Map(), visibilityListeners = new Set();
  const document = {
    hidden: false,
    addEventListener(type, callback) { assert.equal(type, 'visibilitychange'); visibilityListeners.add(callback); },
    removeEventListener(type, callback) { assert.equal(type, 'visibilitychange'); visibilityListeners.delete(callback); },
  };
  const effects = [], cleanup = [], errors = [], probes = [];
  const src = 'http://127.0.0.1:4242/private-login#eva-main';
  const child = { postMessage(message, origin) { probes.push({ message, origin }); } };
  const frameRef = { current: { contentWindow: child, getBoundingClientRect() { return { left: 200, top: 0, width: 800, height: 600 }; } } };
  const React = {
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
    useSyncExternalStore(_subscribe, snapshot) { return snapshot(); },
    useRef() { return frameRef; },
    useState(value) {
      if (framePhase) return [ready, (next) => { ready = next; }];
      return [typeof value === 'string' ? src : value, (next) => errors.push(next)];
    },
    useEffect(callback) { if (framePhase && collect) effects.push(callback); },
    useLayoutEffect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
  };
  runInNewContext(await readFile(new URL('../client.js', import.meta.url), 'utf8'), {
    window: {
      __ModuleLoader__: { load({ factory }) { plugin = factory(() => React); } },
      addEventListener(type, callback) { if (type === 'message') listeners.add(callback); },
      removeEventListener(type, callback) { if (type === 'message') listeners.delete(callback); },
    },
    globalThis: { __DSH_TRANSPORT__: { fetch: async () => ({ ok: true, json: async () => ({ mode: 'parent', nodes: [{ handle: 'eva-001', status: 'running', mainSessionId: 'fixed-main' }] }) }) } },
    AbortController, URL, document,
    ResizeObserver: class { observe() {} disconnect() {} }, requestAnimationFrame() { return 1; }, cancelAnimationFrame() {},
    setTimeout(callback, ms) { const id = Symbol('timer'); timers.set(id, { callback, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  plugin.apply({
    effect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
    locale: { register() { return () => {}; }, bind() { return (key) => key; }, getSnapshot() { return 'en'; }, subscribe() { return () => {}; } },
    slots: {
      inject(_name, callback) { return callback(); },
      register(options, component) { active.set(`${options.name}/${options.key ?? options.id}`, component); return () => active.delete(`${options.name}/${options.key ?? options.id}`); },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  const anchor = active.get('main/eva:eva-001')();
  anchor.type(anchor.props);
  const deckElement = active.get('shell.overlay/eva:retained-frames')({ usePanelInfo: (select) => select({ activePanelId: 'eva:eva-001' }) });
  const deck = deckElement.type(deckElement.props);
  const main = deck.props.children[1][0].props.children[0];
  const frame = main.type(main.props);
  framePhase = true;
  const render = () => frame.type(frame.props);
  const declaration = render();
  for (const effect of effects) cleanup.push(effect());
  collect = false;
  return {
    declaration, render, errors, probes, timers, listeners, child, visibilityListeners,
    visibility(hidden) { document.hidden = hidden; for (const callback of visibilityListeners) callback(); },
    message(data = { type: 'eva:main-state', sessionId: 'fixed-main', state: 'ready' }, overrides = {}) {
      for (const callback of listeners) callback({ origin: 'http://127.0.0.1:4242', source: child, data, ...overrides });
    },
    deadline() { return [...timers.values()].find(({ ms }) => ms === 30000); },
    dispose() { for (const dispose of cleanup.splice(0).reverse()) dispose?.(); },
  };
}

function assertCovered(ui, covered) {
  const declaration = ui.render();
  const frame = declaration.props.children[0];
  assert.equal(declaration.props['aria-busy'], covered);
  assert.equal(frame.type, 'iframe');
  assert.equal(frame.props.style.display, 'block', 'preserve real layout and render scheduling');
  assert.equal(frame.props.style.opacity, covered ? 0 : 1);
  assert.equal(frame.props.style.pointerEvents, covered ? 'none' : 'auto');
  assert.equal(frame.props['aria-hidden'], covered);
  assert.equal(frame.props.inert, covered, 'pending frame must not steal focus');
  assert.equal(frame.props.tabIndex, covered ? -1 : 0);
  assert.equal(Boolean(declaration.props.children[1]), covered);
  assert.ok(!frame.props.sandbox.includes('allow-top-navigation'));
}

test('parent covers the mounted iframe until its exact child confirms the main view', async () => {
  const ui = await fixture();
  try {
    assertCovered(ui, true);
    assert.equal(ui.probes[0].message.type, 'eva:main-state-request');
    assert.equal(ui.probes[0].origin, 'http://127.0.0.1:4242');
    assert.ok(ui.deadline());
    ui.message(); assertCovered(ui, false);
    assert.equal(ui.deadline(), undefined);
    ui.message({ type: 'eva:main-state', sessionId: 'fixed-main', state: 'opening' });
    assertCovered(ui, true); assert.ok(ui.deadline(), 'reload/retry re-arms the bounded wait');
    ui.message({ type: 'eva:main-state', sessionId: 'fixed-main', state: 'failed' });
    assertCovered(ui, false); assert.equal(ui.deadline(), undefined, 'show the child error and explicit retry');
  } finally { ui.dispose(); }
  assert.equal(ui.listeners.size, 0);
  assert.equal(ui.timers.size, 0);
});

test('foreign, stale, wrong-main and malformed readiness messages cannot expose the frame', async () => {
  const ui = await fixture();
  try {
    for (const overrides of [{ source: {} }, { source: null }, { origin: 'http://127.0.0.1:4243' }, { origin: 'null' }, { origin: 'http://127.0.0.1:4242.evil.test' }]) {
      ui.message(undefined, overrides); assertCovered(ui, true);
    }
    for (const data of [null, 'ready', {}, { type: 'eva:main-state', sessionId: 'other-main', state: 'ready' }, { type: 'eva:main-state', sessionId: 'fixed-main', state: 'loaded' }, { type: 'other', sessionId: 'fixed-main', state: 'ready' }]) {
      ui.message(data); assertCovered(ui, true);
    }
    assert.ok(ui.deadline());
  } finally { ui.dispose(); }
});

test('background tabs suspend the deadline while child painting may be paused', async () => {
  const ui = await fixture();
  try {
    const staleDeadline = ui.deadline();
    ui.visibility(true); assert.equal(ui.deadline(), undefined);
    staleDeadline.callback(); assert.deepEqual(ui.errors, []);
    ui.visibility(false); assert.ok(ui.deadline());
    assert.equal(ui.probes.length, 2, 'probe again after returning to the page');
    const resumedDeadline = ui.deadline();
    ui.message(); resumedDeadline.callback(); assert.deepEqual(ui.errors, [false], 'valid readiness clears an error; a late timer cannot restore it');
    ui.visibility(true); ui.visibility(false);
    assert.equal(ui.deadline(), undefined, 'ready frames do not acquire a fresh deadline');
    assert.equal(ui.probes.length, 2);
  } finally { ui.dispose(); }
  assert.equal(ui.visibilityListeners.size, 0);
});

test('missing handshake and iframe errors have an explicit retry path; disposal blocks late timers', async () => {
  const ui = await fixture();
  const deadline = ui.deadline();
  deadline.callback(); assert.deepEqual(ui.errors, [true]);
  ui.render().props.children[0].props.onError(); assert.deepEqual(ui.errors, [true, true]);
  ui.dispose(); deadline.callback();
  assert.deepEqual(ui.errors, [true, true]);
  assert.equal(ui.listeners.size, 0);
  assert.equal(ui.timers.size, 0);
});
