import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

const tick = () => new Promise(resolve => setImmediate(resolve));
const node = (n, patch = {}) => ({ handle: `eva-00${n}`, status: 'running', mainSessionId: `main-${n}`, workspace: `/eva-${n}`, instanceId: `generation-${n}`, ...patch });

// Narrow hook/declaration fixture: exercise the actual cache declarations and
// effect dependencies. This is not a DOM renderer, browser, or iframe emulator.
async function fixture({ deferred = false } = {}) {
  let plugin, refresh, current, activePanelId = null, anchorOwner, visit = 0;
  let nodes = [0, 1, 2, 3].map(n => node(n)), unavailable = false;
  const entries = new Map(), owners = new Map(), cleanup = [], requests = [], pending = [], providers = new Map();
  let clock = 0;
  const windowListeners = new Map(), documentListeners = new Map(), timers = new Map(), paints = new Map(), observers = new Set();
  const bounds = { left: 264, top: 0, width: 800, height: 600 };
  const ownAnchor = { getBoundingClientRect: () => ({ ...bounds }) };
  const document = { hidden: false,
    addEventListener(type, callback) { if (!documentListeners.has(type)) documentListeners.set(type, new Set()); documentListeners.get(type).add(callback); },
    removeEventListener(type, callback) { documentListeners.get(type)?.delete(callback); },
  };
  const React = {
    Fragment: Symbol('Fragment'),
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
    useState(initial) {
      const owner = current, index = owner.cursor++;
      if (!owner.hooks[index]) {
        const state = { value: initial };
        state.set = value => { state.value = typeof value === 'function' ? value(state.value) : value; };
        owner.hooks[index] = state;
      }
      const state = owner.hooks[index]; return [state.value, state.set];
    },
    useRef(initial) {
      const owner = current, index = owner.cursor++;
      return owner.hooks[index] ??= { current: owner.name.startsWith('anchor:') ? ownAnchor : { contentWindow: { postMessage() {} } } };
    },
    useEffect(callback, dependencies) {
      const owner = current, index = owner.cursor++, previous = owner.hooks[index];
      if (previous && dependencies?.length === previous.dependencies?.length && dependencies.every((item, i) => Object.is(item, previous.dependencies[i]))) return;
      owner.effects.push(() => { previous?.dispose?.(); owner.hooks[index] = { dependencies, dispose: callback() }; });
    },
    useLayoutEffect(callback, dependencies) { return this.useEffect(callback, dependencies); },
    useSyncExternalStore(subscribe, snapshot) { this.useEffect(() => subscribe(() => {}), []); return snapshot(); },
  };
  function run(name, element) {
    let owner = owners.get(name);
    if (!owner) { owner = { name, hooks: [] }; owners.set(name, owner); }
    current = owner; owner.cursor = 0; owner.effects = [];
    const result = element.type(element.props); current = null;
    for (const effect of owner.effects) effect();
    return result;
  }
  function disposeOwner(name) {
    const owner = owners.get(name); owners.delete(name);
    for (const hook of owner?.hooks ?? []) hook?.dispose?.();
  }
  const window = {
    __ModuleLoader__: { load({ factory }) { plugin = factory(() => React); } },
    addEventListener(type, callback) { if (!windowListeners.has(type)) windowListeners.set(type, new Set()); windowListeners.get(type).add(callback); },
    removeEventListener(type, callback) { windowListeners.get(type)?.delete(callback); },
  };
  runInNewContext(await readFile(new URL('../client.js', import.meta.url), 'utf8'), {
    window, document, AbortController, URL,
    ResizeObserver: class { constructor(callback) { this.callback = callback; observers.add(this); } observe() {} disconnect() { observers.delete(this); } },
    requestAnimationFrame(callback) { const id = Symbol('paint'); paints.set(id, callback); return id; },
    cancelAnimationFrame(id) { paints.delete(id); },
    setTimeout(callback, ms) { if (ms === 3000) refresh = callback; const id = Symbol('timer'); timers.set(id, { callback, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    globalThis: { performance: { now: () => clock }, __DSH_TRANSPORT__: { fetch(path, init) {
      if (path === 'api/eva/view') return unavailable
        ? Promise.reject(new Error('temporary connection loss'))
        : Promise.resolve({ ok: true, json: async () => ({ mode: 'parent', nodes }) });
      assert.ok(path.startsWith('api/eva/open?handle='));
      requests.push({ path, init });
      // Production launchUrl is cached for a running Host; retries can return
      // exactly the same URL. Never use changing mock URLs to force remounts.
      const response = { ok: true, json: async () => ({ url: 'http://127.0.0.1:4242/login-1#eva-main' }) };
      return deferred ? new Promise(resolve => pending.push(() => resolve(response))) : Promise.resolve(response);
    } } },
  });
  plugin.apply({
    inject(names, callback) {
      assert.deepEqual(Array.from(names), ['cordisInspect']);
      callback({
        effect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
        cordisInspect: { register(provider) {
          assert.equal(providers.has(provider.manifest.id), false);
          providers.set(provider.manifest.id, provider);
          return () => providers.delete(provider.manifest.id);
        } },
      });
    },
    effect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
    locale: { register() { return () => {}; }, bind() { return key => key; }, subscribe() { return () => {}; }, getSnapshot() { return 'en'; } },
    slots: {
      inject(name, callback) { assert.ok(['main', 'shell.overlay'].includes(name)); return callback(); },
      register(options, component) { const id = `${options.name}/${options.key ?? options.id}`; assert.equal(entries.has(id), false); entries.set(id, component); return () => entries.delete(id); },
    },
  });
  await tick();
  function deck() {
    const registration = entries.get('shell.overlay/eva:retained-frames');
    return run('deck', registration({ usePanelInfo: select => select({ activePanelId }) }));
  }
  const panels = () => deck().props.children[1];
  function conversations() {
    const retained = panels(), keys = new Set(retained.map(panel => panel.props.key));
    // Explicitly exercise cleanup when the cache stops declaring an owner.
    for (const name of [...owners.keys()]) if (/^(conversation|frame):/.test(name) && !keys.has(Number(name.split(':')[1]))) disposeOwner(name);
    return retained.map(panel => {
      const element = run(`conversation:${panel.props.key}`, panel.props.children[0]);
      // Explicit child-key lifecycle checks only; still no renderer/DOM.
      const expected = typeof element.type === 'function' ? `frame:${panel.props.key}:${element.props.key}` : null;
      for (const name of [...owners.keys()]) if (name.startsWith(`frame:${panel.props.key}:`) && name !== expected) disposeOwner(name);
      return { panel, element };
    });
  }
  function leave() { if (anchorOwner) disposeOwner(anchorOwner); anchorOwner = undefined; activePanelId = null; }
  function enter(handle) {
    leave(); activePanelId = `eva:${handle}`; anchorOwner = `anchor:${++visit}`;
    return run(anchorOwner, entries.get(`main/eva:${handle}`)());
  }
  return {
    requests, pending, entries, timers, paints, observers, bounds, windowListeners, documentListeners, panels, conversations,
    enter, leave, deck, providers,
    advance(ms) { clock += ms; },
    visibility(hidden) { document.hidden = hidden; for (const callback of [...(documentListeners.get('visibilitychange') ?? [])]) callback(); },
    snapshot() { return providers.get('EVAFrameTiming').query('snapshot', {}); },
    paint() { const callbacks = [...paints.values()]; paints.clear(); callbacks.forEach(callback => callback()); },
    message(item, state = 'ready', patch = {}) {
      const source = owners.get(`frame:${item.panel.props.key}:${item.element.props.key}`).hooks[0].current.contentWindow;
      const event = { source, origin: 'http://127.0.0.1:4242', data: { type: 'eva:main-state', sessionId: item.element.props.sessionId, state }, ...patch };
      for (const callback of [...(windowListeners.get('message') ?? [])]) callback(event);
    },
    rerenderAnchor(handle) { return run(anchorOwner, entries.get(`main/eva:${handle}`)()); },
    frame(item) { return run(`frame:${item.panel.props.key}:${item.element.props.key}`, item.element); },
    async poll(next = nodes) { nodes = next; await refresh(); },
    async failPoll() { unavailable = true; try { await refresh(); } finally { unavailable = false; } },
    async flush() { await tick(); return conversations(); },
    dispose() { leave(); for (const dispose of cleanup.splice(0).reverse()) dispose(); for (const name of [...owners.keys()]) disposeOwner(name); },
  };
}

test('A -> main Bot -> A and A -> B -> A retain cache keys and open each visited generation once', async () => {
  const ui = await fixture();
  try {
    assert.equal(ui.panels().length, 0); assert.equal(ui.requests.length, 0, 'never prefetch unvisited Bots');
    ui.enter('eva-000'); ui.conversations(); await ui.flush();
    const first = ui.panels()[0], key = first.props.key;
    ui.leave();
    const inactive = ui.panels()[0];
    assert.equal(inactive.props.key, key);
    assert.equal(inactive.props.inert, true); assert.equal(inactive.props['aria-hidden'], true);
    assert.equal(inactive.props.style.visibility, 'hidden'); assert.equal(inactive.props.style.width, 800);
    assert.notEqual(inactive.props.style.display, 'none');
    ui.conversations(); ui.enter('eva-000'); ui.conversations();
    assert.equal(ui.panels()[0].props.key, key); assert.equal(ui.requests.length, 1);
    ui.enter('eva-001'); ui.conversations(); await ui.flush();
    ui.enter('eva-000'); const views = ui.conversations();
    assert.equal(ui.requests.length, 2);
    assert.equal(views.find(view => view.panel.props['data-eva-retained-panel'] === 'eva-000').panel.props.key, key);
    assert.equal(views[0].element.props.src, 'http://127.0.0.1:4242/login-1#eva-main');
    const registrations = [...ui.entries.values()];
    await ui.poll([node(0, { preview: { state: 'message', text: 'A new assistant reply' } }), node(1), node(2), node(3)]);
    assert.deepEqual([...ui.entries.values()], registrations, 'fresh message-preview polling preserves main and deck registration identity');
    ui.rerenderAnchor('eva-000'); ui.conversations(); assert.equal(ui.requests.length, 2);
    assert.equal(ui.panels()[0].props.key, key, 'message updates preserve the retained generation key');
    assert.match(first.props.style.clipPath, /--dsh-frame-leading-clearance/);
    assert.match(ui.deck().props.children[0].props.children[0], /\[data-rightbar-fullscreen\] \[data-eva-retained-panel\]/);
  } finally { ui.dispose(); }
});

test('visited pages have no count eviction and invalidate only stopped/removed/restarted generations', async () => {
  const ui = await fixture();
  try {
    for (const n of [0, 1, 2, 0, 3]) { ui.enter(`eva-00${n}`); ui.conversations(); await ui.flush(); }
    assert.equal(ui.requests.length, 4);
    assert.deepEqual(Array.from(ui.panels(), panel => panel.props['data-eva-retained-panel']).sort(), ['eva-000', 'eva-001', 'eva-002', 'eva-003']);
    ui.enter('eva-001'); ui.conversations(); await ui.flush(); assert.equal(ui.requests.length, 4, 'fourth visit never evicts an earlier page');
    const key = ui.panels().find(panel => panel.props['data-eva-retained-panel'] === 'eva-001').props.key;
    await ui.poll([node(0), node(1, { instanceId: 'restarted' }), node(2), node(3)]);
    ui.rerenderAnchor('eva-001'); ui.conversations(); await ui.flush();
    assert.notEqual(ui.panels().find(panel => panel.props['data-eva-retained-panel'] === 'eva-001').props.key, key);
    assert.equal(ui.requests.length, 5);
    await ui.poll([node(0), node(1, { status: 'stopped' }), node(2), node(3)]);
    const offline = ui.rerenderAnchor('eva-001');
    assert.equal(offline.type.name, 'MainConversation', 'selected stopped node keeps the original offline/wake fallback');
    assert.ok(!ui.panels().some(panel => panel.props['data-eva-retained-panel'] === 'eva-001'));
    ui.conversations(); await ui.poll([node(0), node(3)]);
    assert.ok(!ui.entries.has('main/eva:eva-002'));
    assert.ok(!ui.panels().some(panel => panel.props['data-eva-retained-panel'] === 'eva-002'));
  } finally { ui.dispose(); }
});

test('generation/removal invalidation aborts authentication while visits do not; late responses are ignored', async () => {
  const ui = await fixture({ deferred: true });
  try {
    ui.enter('eva-000'); ui.conversations();
    const oldRequest = ui.requests[0];
    await ui.poll([node(0, { instanceId: 'new' }), node(1), node(2), node(3)]);
    ui.rerenderAnchor('eva-000'); ui.conversations();
    assert.equal(oldRequest.init.signal.aborted, true);
    ui.pending[0](); await tick();
    assert.equal(ui.conversations()[0].element.type, 'section', 'late old login cannot enter new generation');
    for (const n of [1, 2, 3]) { ui.enter(`eva-00${n}`); ui.conversations(); }
    assert.equal(ui.requests[1].init.signal.aborted, false, 'switching across four pages retains in-flight navigation');
    assert.equal(ui.panels().length, 4);
    await ui.poll([node(1), node(2), node(3)]); ui.conversations();
    assert.equal(ui.requests[1].init.signal.aborted, true, 'actual removal releases the pending navigation');
    for (const resolve of ui.pending) resolve(); await tick();
    assert.equal(ui.panels().length, 3);
  } finally { ui.dispose(); }
  assert.ok(ui.requests.every(request => request.init.signal.aborted));
});

test('own geometry subscriptions are bounded and teardown releases timers/listeners/observers', async () => {
  const ui = await fixture();
  ui.enter('eva-000'); ui.conversations(); const [item] = await ui.flush(); ui.frame(item);
  assert.equal([...ui.timers.values()].filter(timer => timer.ms === 30000).length, 1);
  ui.leave(); const [inactive] = ui.conversations(); ui.frame(inactive);
  assert.equal([...ui.timers.values()].filter(timer => timer.ms === 30000).length, 0, 'inactive cached iframe has no visible-load deadline');
  ui.enter('eva-000'); ui.conversations(); ui.paint();
  assert.equal(ui.observers.size, 1);
  const resize = [...ui.windowListeners.get('resize')][0]; resize(); resize();
  assert.equal(ui.paints.size, 1, 'coalesce geometry events, never a continuous measurement loop');
  ui.bounds.width = 700;
  const callbacks = [...ui.paints.values()]; ui.paints.clear(); callbacks.forEach(callback => callback());
  assert.equal(ui.panels()[0].props.style.width, 700);
  const observer = [...ui.observers][0];
  ui.dispose(); resize(); observer.callback();
  assert.equal(ui.entries.size, 0); assert.equal(ui.observers.size, 0); assert.equal(ui.paints.size, 0, 'late geometry callbacks cannot schedule work after teardown');
  assert.ok([...ui.windowListeners.values()].every(listeners => listeners.size === 0));
  assert.ok([...ui.documentListeners.values()].every(listeners => listeners.size === 0));
  assert.equal([...ui.timers.values()].filter(timer => timer.ms === 30000).length, 0);
});

test('eight visited pages survive transient inventory failures with stable keys and no reauthentication', async () => {
  const ui = await fixture();
  try {
    await ui.poll(Array.from({ length: 8 }, (_, n) => node(n)));
    assert.equal(ui.panels().length, 0, 'inventory alone never opens frames');
    for (let n = 0; n < 8; n++) { ui.enter(node(n).handle); ui.conversations(); await ui.flush(); }
    const keys = Array.from(ui.panels(), panel => panel.props.key);
    await ui.failPoll();
    assert.deepEqual(Array.from(ui.panels(), panel => panel.props.key), keys);
    for (let n = 0; n < 8; n++) { ui.enter(node(n).handle); ui.conversations(); await ui.flush(); }
    assert.equal(ui.requests.length, 8); assert.equal(ui.panels().length, 8);
    const snapshot = ui.snapshot();
    assert.equal(snapshot.retainedCount, 8); assert.equal(snapshot.pageLimit, null);
  } finally { ui.dispose(); }
});

test('read-only diagnostics separate genuine child readiness from anchor scheduling and failures', async () => {
  const ui = await fixture();
  try {
    ui.enter('eva-000'); ui.conversations();
    ui.advance(10); const [item] = await ui.flush(); ui.frame(item);
    ui.advance(30); ui.message(item, 'ready', { source: {} });
    assert.equal(ui.snapshot().frames[0].initialChildReadyMs, null, 'untrusted message cannot settle timing');
    ui.message(item);
    let timing = ui.snapshot().frames[0];
    assert.equal(timing.initialChildReadyMs, 40); assert.equal(timing.initialPreparationMs, 10);
    assert.equal(timing.firstOutcome, 'ready');
    ui.advance(50); ui.message(item);
    assert.equal(ui.snapshot().frames[0].initialChildReadyMs, 40, 'duplicate readiness never rewrites initial duration');
    ui.leave(); ui.enter('eva-000'); ui.conversations(); ui.advance(7); ui.paint();
    timing = ui.snapshot().frames[0];
    assert.equal(timing.visits, 2); assert.equal(timing.openRequests, 1);
    assert.equal(timing.readyOnLastVisit, true); assert.equal(timing.anchorToAnimationFrameMs, 7);
    assert.equal(timing.initialChildReadyMs, 40);
    ui.enter('eva-001'); ui.conversations(); ui.visibility(true);
    const items = await ui.flush(); ui.visibility(false);
    assert.equal(ui.snapshot().frames[1].backgroundDuringInitialOpen, true, 'record hidden time before iframe exists');
    const failed = items.find(item => item.panel.props['data-eva-retained-panel'] === 'eva-001'); ui.frame(failed);
    ui.advance(5); ui.message(failed, 'failed');
    const beforeRecovery = ui.snapshot().frames[1];
    assert.equal(beforeRecovery.firstOutcome, 'child-failed'); assert.equal(beforeRecovery.initialChildReadyMs, null);
    ui.advance(10); ui.message(failed, 'ready');
    const recovered = ui.snapshot().frames[1];
    assert.equal(recovered.firstOutcome, 'child-failed'); assert.equal(recovered.recoveredReadyMs, 15);
    const before = ui.requests.length;
    const snapshot = ui.snapshot(); snapshot.frames[0].state = 'tampered';
    assert.equal(ui.requests.length, before, 'query never opens or probes children');
    assert.equal(ui.snapshot().frames[0].state, 'ready', 'snapshot has no mutable internal references');
    const encoded = JSON.stringify(ui.snapshot());
    for (const privateValue of ['login-', 'http:', 'main-0', '/eva-0', 'generation-0']) assert.equal(encoded.includes(privateValue), false);
    assert.equal(ui.snapshot().memory.status, 'not-measured'); assert.equal(ui.snapshot().memory.perFrameBytes, null);
  } finally { ui.dispose(); }
  assert.equal(ui.providers.size, 0, 'provider belongs to plugin lifetime');
});

test('iframe timeout/error preserves its page and trusted readiness recovers without another open request', async () => {
  const ui = await fixture();
  try {
    ui.enter('eva-000'); ui.conversations(); const [initial] = await ui.flush();
    const initialDeclaration = ui.frame(initial);
    const deadline = [...ui.timers.values()].find(timer => timer.ms === 30000);
    ui.advance(30000); deadline.callback();
    let [covered] = ui.conversations();
    assert.equal(covered.element.type, initial.element.type);
    assert.equal(covered.element.props.key, initial.element.props.key);
    assert.equal(covered.element.props.error, true);
    assert.equal(ui.frame(covered).props.children[0].type, 'iframe', 'error overlay does not remove iframe declaration');
    assert.equal(ui.requests.length, 1);
    ui.advance(15); ui.message(covered);
    let [recovered] = ui.conversations();
    assert.equal(recovered.element.props.error, false);
    assert.equal(ui.frame(recovered).props.children[0].props.src, initialDeclaration.props.children[0].props.src);
    assert.equal(ui.snapshot().frames[0].firstOutcome, 'timeout');
    assert.equal(ui.snapshot().frames[0].initialChildReadyMs, null);
    assert.equal(ui.snapshot().frames[0].recoveredReadyMs, 30015);
    ui.frame(recovered).props.children[0].props.onError();
    [covered] = ui.conversations(); ui.frame(covered);
    assert.equal(covered.element.props.error, true); assert.equal(ui.requests.length, 1);
    covered.element.props.onRetry();
    const [preparing] = ui.conversations();
    assert.notEqual(preparing.element.type, initial.element.type, 'retry epoch rejects stale src immediately, independent of blank-state batching');
    const [retried] = await ui.flush();
    assert.equal(ui.requests.length, 2, 'only explicit retry prepares a fresh page');
    assert.equal(retried.element.props.src, initial.element.props.src, 'same Host returns same navigation URL');
    assert.notEqual(retried.element.props.key, initial.element.props.key, 'same URL still gets a fresh retry key');
    assert.equal(ui.frame(retried).props.children[0].props['aria-hidden'], true, 'old ready state cannot reveal retry before its handshake');
  } finally { ui.dispose(); }
});
