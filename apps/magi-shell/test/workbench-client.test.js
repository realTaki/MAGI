import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

const source = await readFile(new URL('../client.js', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const mainBot = { handle: 'dsh-main', kind: 'parent', status: 'running', workspace: '/parent/workspace', mainSessionId: 'fixed-native-main' };
const running = { handle: 'eva-000', status: 'running', mainSessionId: 'eva-main-0' };

// Shallow native-slot/handler fixture: not a DOM renderer or visual verification.
function harness({ embedded = false, mode = 'parent', nodes = [running], bot = mainBot,
  href = 'http://127.0.0.1:57684/', fetchView, fetchMain, fetchCreate, failMain = false, registeredEva = ['eva-000'] } = {}) {
  let plugin, locale = 'en', snapshot, nextTimer = 0, currentHooks, cursor, onToggle, navigationController;
  const active = new Map(), cleanups = [], timers = new Map(), calls = [], dictionaries = new Map();
  const injections = [], registrations = [], navigation = [], hooks = new Map(), panels = new Set(registeredEva.map(handle => `eva:${handle}`));
  const React = {
    Fragment: Symbol('fragment'),
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
    useSyncExternalStore(_subscribe, getSnapshot) { const value = getSnapshot(); if (value && Array.isArray(value.nodes)) snapshot = value; return value; },
    useState(initial) { const values = currentHooks; const index = cursor++; if (!(index in values)) values[index] = typeof initial === 'function' ? initial() : initial; return [values[index], next => { values[index] = typeof next === 'function' ? next(values[index]) : next; }]; },
    useRef(initial) { return { current: initial }; },
    useEffect() { assert.fail('Native sidebar reuse must not mount a conversation/frame effect'); },
  };
  const window = { __ModuleLoader__: { load({ factory }) { plugin = factory(name => { assert.equal(name, 'react'); return React; }); } } };
  window.parent = embedded ? {} : window;
  runInNewContext(source, {
    window, location: { href }, URL, URLSearchParams, AbortController,
    setTimeout(fn) { const id = ++nextTimer; timers.set(id, fn); return id; }, clearTimeout(id) { timers.delete(id); },
    globalThis: { fetch() { assert.fail('Never bypass authenticated transport'); }, __DSH_TRANSPORT__: { async fetch(path, options) {
      calls.push({ path, options });
      assert.ok(['api/eva/view', 'api/eva/parent-main', 'api/eva/create'].includes(path), 'no iframe credentials, main-open, or Session mutation endpoints');
      if (path === 'api/eva/create') {
        assert.equal(options.method, 'POST');
        return fetchCreate ? fetchCreate(options) : { ok: true, json: async () => ({ handle: 'eva-001', status: 'running', mainSessionId: 'new-eva-main' }) };
      }
      assert.equal(options.method, undefined);
      if (path === 'api/eva/view') return fetchView ? fetchView(options) : { ok: true, json: async () => ({ mode, nodes }) };
      return fetchMain ? fetchMain(options) : bot ? { ok: true, json: async () => ({ mode: 'parent', bot }) } : { ok: false, status: 404 };
    } } },
  });
  const forbidden = new Proxy({}, { get(_target, key) { assert.fail(`No catalog reads, retention, creation, or replacement Session APIs: ${String(key)}`); } });
  plugin.apply({
    effect(fn) { const dispose = fn(); if (dispose) cleanups.push(dispose); },
    locale: {
      register(namespace, language, dict) { assert.equal(namespace, 'magi-shell'); dictionaries.set(language, dict); return () => dictionaries.delete(language); },
      bind() { return key => dictionaries.get(locale)[key]; }, subscribe() { return () => {}; }, getSnapshot() { return locale; },
    },
    slots: {
      inject(name, fn) {
        assert.ok(['sidebar.brand.name', 'sidebar.footer.action', 'sidebar', 'shell.leading'].includes(name), 'only scoped sidebar/chrome seats; native root, main and overlay stay untouched');
        injections.push(name); const dispose = fn(); let disposed = false;
        const once = () => { if (!disposed) { disposed = true; dispose?.(); } }; cleanups.push(once); return once;
      },
      register(options, component) {
        assert.equal(active.has(options.name), false); registrations.push(options); const entry = { options, component }; active.set(options.name, entry);
        return () => { if (active.get(options.name) === entry) active.delete(options.name); };
      },
    },
    sessions: forbidden, workspaces: forbidden,
    uiWorkspace: {
      openSession(id) { navigation.push({ session: id }); if (failMain) throw new Error('private native failure'); },
      startSession() { assert.fail('Never create a replacement conversation'); },
      openWorkspace() { assert.fail('Never adopt a Workspace conversation'); },
    },
    layout: { beginNavigation() { navigationController?.abort(); navigationController = new AbortController(); return navigationController.signal; }, toggleSidebar() { onToggle?.(); }, selectPanel(id) { navigation.push({ panel: id }); if (id !== null && !panels.has(id)) throw new Error('EVA panel not registered yet'); } },
  });
  function render(element) {
    if (!element || typeof element !== 'object' || !element.props) return element;
    if (typeof element.type === 'function') {
      if (!hooks.has(element.type)) hooks.set(element.type, []);
      currentHooks = hooks.get(element.type); cursor = 0;
      return render(element.type(element.props));
    }
    return { ...element, props: { ...element.props, children: element.props.children.flat(Infinity).map(render) } };
  }
  const slot = (name, props = {}) => {
    const entry = active.get(name); if (!entry) return null;
    currentHooks = hooks.get(entry.component) ?? []; hooks.set(entry.component, currentHooks); cursor = 0;
    return render(entry.component(props));
  };
  return { active, calls, timers, injections, registrations, navigation, panels,
    get snapshot() { return snapshot; }, setLocale(value) { locale = value; }, setFailMain(value) { failMain = value; },
    sidebar(props) { return slot('sidebar', props); }, leading() { return slot('shell.leading'); },
    browser(props = {}) { onToggle = props.expandSidebar; return elements(slot('sidebar', { collapsed: props.wide === false })).find(element => element.props.className === 'magi-wb-browser') ?? null; }, brand() { return slot('sidebar.brand.name'); },
    switch() { return elements(slot('sidebar.footer.action', { wide: false })).find(element => element.type === 'button'); },
    expandedSwitch() { return slot('sidebar.footer.action', { wide: true }); },
    async poll() { assert.equal(timers.size, 1); const [id, callback] = timers.entries().next().value; timers.delete(id); await callback(); await tick(); },
    dispose() { for (const fn of cleanups.reverse()) fn(); },
  };
}
function elements(element) {
  if (!element || typeof element !== 'object' || !element.props) return [];
  return [element, ...element.props.children.flat(Infinity).flatMap(elements)];
}
const rows = tree => elements(tree).filter(element => element.props.className === 'magi-wb-node');
const search = tree => elements(tree).find(element => element.type === 'input');
const text = tree => elements(tree).flatMap(element => element.props.children).filter(child => typeof child === 'string');
const retry = tree => elements(tree).find(element => element.type === 'button' && (element.props.children.includes('Retry') || element.props['aria-label'] === 'Retry'));
async function open(app) { await tick(); app.switch().props.onClick(); await tick(); return app.browser(); }

const newBotButton = (app) => elements(app.sidebar()).find(element => element.props.className === 'magi-wb-new');
const createCalls = app => app.calls.filter(call => call.path === 'api/eva/create');

test('New Bot uses an icon and full-width outlined surface; compact chrome keeps the same accessible action', async () => {
  const app = harness();
  try {
    await open(app);
    const button = newBotButton(app);
    assert.equal(button.props['data-compact'], 'false');
    assert.equal(elements(button).filter(element => element.type === 'svg').length, 1);
    assert.ok(text(button).includes('New Bot'));
    assert.equal(elements(button).find(element => element.type === 'svg').props['aria-hidden'], true);
    for (const tree of [app.sidebar({ collapsed: true }), app.leading()]) {
      const compact = elements(tree).find(element => element.props.className === 'magi-wb-new');
      assert.equal(compact.props['data-compact'], 'true');
      assert.equal(compact.props['aria-label'], 'New Bot');
      assert.equal(elements(compact).filter(element => element.type === 'svg').length, 1);
      assert.deepEqual(text(compact), [], 'compact action is an icon, not a plus glyph or clipped label');
    }
    const styles = elements(app.sidebar()).find(element => element.type === 'style').props.children.join('');
    assert.match(styles, /\.magi-wb-new \{[^}]*align-self:stretch[^}]*min-height:36px[^}]*border:1px solid var\(--dsw-alias-border-l1\)[^}]*border-radius:10px[^}]*background:var\(--dsw-alias-bg-base\)/);
    assert.match(styles, /\.magi-wb-new:focus-visible/);
    assert.match(styles, /\.magi-wb-new:hover:not\(:disabled\)/);
    assert.equal(createCalls(app).length, 0, 'styling/rendering never creates a Bot');
    app.setLocale('zh'); assert.ok(text(newBotButton(app)).includes('新 Bot'));
  } finally { app.dispose(); }
});

test('original MAGI mark appears only in its workbench header and never replaces native identity artwork', async () => {
  const app = harness();
  const marks = tree => elements(tree).filter(element => element.props.className === 'magi-wb-mark');
  try {
    await tick();
    assert.equal(marks(app.brand()).length, 0);
    await open(app);
    const header = elements(app.sidebar()).find(element => element.props.className === 'magi-wb-brand');
    assert.equal(header.props['data-mode'], 'magi');
    const [mark] = marks(header);
    assert.equal(marks(header).length, 1);
    assert.equal(mark.type, 'svg');
    assert.equal(mark.props['aria-hidden'], true);
    assert.equal(mark.props.width, 28);
    assert.ok(elements(mark).some(element => element.props.d === 'M15 39V23L23 33L31 23V39'));
    assert.ok(elements(header).some(element => element.props['aria-label'] === 'Switch back to DSH'));
    app.switch().props.onClick();
    assert.equal(marks(app.brand()).length, 0);
    assert.equal(createCalls(app).length, 0);
  } finally { app.dispose(); }
});

test('sidebar toggle is a panel icon in its own top chrome row, never a brand-row chevron', async () => {
  const app = harness();
  try {
    await open(app);
    let toggles = 0; app.browser({ expandSidebar() { toggles++; } });
    const tree = app.sidebar({ collapsed: false });
    const chrome = elements(tree).find(element => element.props.className === 'magi-wb-chrome');
    const brandRow = elements(tree).find(element => element.props.className === 'magi-wb-sidebar-top');
    const toggle = elements(chrome).find(element => element.type === 'button');
    assert.equal(toggle.props['aria-label'], 'Collapse sidebar'); assert.equal(toggle.props['aria-expanded'], true);
    assert.equal(elements(toggle).filter(element => element.type === 'rect').length, 1);
    assert.equal(elements(brandRow).some(element => ['Collapse sidebar', 'Expand sidebar'].includes(element.props['aria-label'])), false);
    toggle.props.onClick();
    for (const compact of [app.sidebar({ collapsed: true }), app.leading()]) {
      const reopen = elements(compact).find(element => element.props['aria-label'] === 'Expand sidebar');
      assert.equal(reopen.props['aria-expanded'], false);
      assert.equal(elements(reopen).filter(element => element.type === 'svg').length, 1);
      reopen.props.onClick();
    }
    assert.equal(toggles, 3, 'all three controls delegate to the native sidebar action');
    assert.equal(text(tree).some(value => ['‹', '›'].includes(value)), false);
    assert.match(source, /html\[data-platform=darwin\] \.magi-wb-chrome \{ height:52px/);
    assert.ok(!source.includes('.magi-wb-sidebar-top { padding-top:52px'));
    assert.equal(createCalls(app).length, 0);
  } finally { app.dispose(); }
});

test('MAGI sidebar and hidden-sidebar chrome own only Bot actions and restore both native seats on close', async () => {
  const app = harness();
  try {
    await open(app);
    assert.ok(text(app.sidebar()).includes('New Bot'));
    for (const forbidden of ['New Session', 'Plugins', 'Automation Tasks', 'More']) assert.ok(!text(app.sidebar()).includes(forbidden));
    assert.equal(createCalls(app).length, 0, 'open/poll/render never start nodes');
    assert.ok(elements(app.leading()).some(element => element.props['aria-label'] === 'New Bot'));
    app.setLocale('zh'); assert.equal(newBotButton(app).props['aria-label'], '新 Bot');
    app.switch().props.onClick();
    assert.equal(app.active.has('sidebar'), false); assert.equal(app.active.has('shell.leading'), false);
    assert.equal(createCalls(app).length, 0);
  } finally { app.dispose(); }
});

test('New Bot explicitly creates one EVA, deduplicates clicks, refreshes and selects its existing panel', async () => {
  let resolve;
  const app = harness({ registeredEva: ['eva-000', 'eva-001'], fetchCreate: () => new Promise(yes => { resolve = yes; }) });
  try {
    await open(app); const button = newBotButton(app); button.props.onClick(); button.props.onClick();
    assert.equal(createCalls(app).length, 1); assert.equal(newBotButton(app).props.disabled, true);
    assert.equal(newBotButton(app).props['aria-label'], 'Creating Bot…');
    const beforeRefresh = app.calls.length;
    resolve({ ok: true, json: async () => ({ handle: 'eva-001', status: 'running', mainSessionId: 'new-eva-main' }) }); await tick();
    assert.ok(app.calls.length > beforeRefresh); assert.equal(newBotButton(app).props.disabled, false);
    assert.deepEqual(app.navigation.at(-1), { panel: 'eva:eva-001' });
    assert.equal(app.navigation.filter(row => row.session).length, 1, 'only original Main Bot opened; no native Session creation');
    assert.ok(rows(app.browser()).some(row => row.props.key === 'eva-001'));
  } finally { app.dispose(); }
});

test('New Bot panel registration race retries navigation to same Bot, never another POST', async () => {
  const app = harness();
  try {
    await open(app); newBotButton(app).props.onClick(); await tick(); app.browser();
    assert.equal(app.snapshot.navError, true); assert.equal(app.snapshot.failedTarget, 'eva-001');
    app.panels.add('eva:eva-001'); await app.poll();
    retry(app.browser()).props.onClick(); app.browser();
    assert.deepEqual(app.navigation.at(-1), { panel: 'eva:eva-001' }); assert.equal(createCalls(app).length, 1);
    assert.equal(app.snapshot.navError, false);
  } finally { app.dispose(); }
});

test('New Bot failures are localized and retry only through another explicit click', async () => {
  let succeeds = false;
  const app = harness({ registeredEva: ['eva-000', 'eva-001'], fetchCreate() { if (!succeeds) throw new Error('private failure'); return { ok: true, json: async () => ({ handle: 'eva-001', status: 'running' }) }; } });
  try {
    await open(app); app.setLocale('zh'); newBotButton(app).props.onClick(); await tick();
    assert.ok(text(app.sidebar()).includes('无法创建 Bot，请检查 EVA 后明确重试。'));
    assert.ok(!text(app.sidebar()).includes('private failure'));
    await app.poll(); assert.equal(createCalls(app).length, 1);
    succeeds = true; const button = elements(app.sidebar()).find(element => element.type === 'button' && element.props.children.includes('重试'));
    button.props.onClick(); await tick(); assert.equal(createCalls(app).length, 2);
    assert.deepEqual(app.navigation.at(-1), { panel: 'eva:eva-001' });
  } finally { app.dispose(); }
});

test('late New Bot completion refreshes inventory but cannot override a newer explicit navigation', async () => {
  let resolve;
  const app = harness({ registeredEva: ['eva-000', 'eva-001'], fetchCreate: () => new Promise(yes => { resolve = yes; }) });
  try {
    await open(app); newBotButton(app).props.onClick();
    rows(app.browser())[1].props.onClick(); const before = app.navigation.length;
    resolve({ ok: true, json: async () => ({ handle: 'eva-001', status: 'running' }) }); await tick();
    assert.equal(app.navigation.length, before); assert.deepEqual(app.navigation.at(-1), { panel: 'eva:eva-000' });
    assert.ok(rows(app.browser()).some(row => row.props.key === 'eva-001')); assert.equal(createCalls(app).length, 1);
  } finally { app.dispose(); }
});

for (const action of ['close', 'dispose']) {
  test(`pending New Bot aborts on ${action}, ignores late response and never recreates automatically`, async () => {
    let resolve;
    const app = harness({ fetchCreate: () => new Promise(yes => { resolve = yes; }) });
    await open(app); newBotButton(app).props.onClick(); const signal = createCalls(app)[0].options.signal;
    if (action === 'close') app.switch().props.onClick(); else app.dispose();
    const before = app.navigation.length; assert.equal(signal.aborted, true);
    resolve({ ok: true, json: async () => ({ handle: 'eva-001', status: 'running' }) }); await tick();
    assert.equal(app.navigation.length, before); assert.equal(createCalls(app).length, 1);
    assert.equal(app.active.has('sidebar'), false); assert.equal(app.active.has('shell.leading'), false);
    if (action === 'close') { app.switch().props.onClick(); await tick(); assert.equal(createCalls(app).length, 1); app.dispose(); }
    assert.equal(app.timers.size, 0);
  });
}

test('Bot avatars are stable solid rounded blocks without robot artwork, retaining presence dots', async () => {
  const app = harness();
  const avatars = () => elements(app.browser()).filter(element => element.props.className === 'magi-wb-avatar');
  try {
    await open(app);
    const initial = avatars().map(avatar => avatar.props.style.background);
    assert.equal(initial.length, 2);
    assert.ok(initial.every(color => /^#[\da-f]{6}$/i.test(color)));
    assert.notEqual(initial[0], initial[1]);
    for (const avatar of avatars()) {
      assert.equal(avatar.props['aria-hidden'], true);
      assert.equal(avatar.props.children.length, 1);
      assert.equal(avatar.props.children[0].props.className, 'magi-wb-dot');
      assert.equal(elements(avatar).some(element => element.type === 'svg'), false);
    }
    await app.poll();
    assert.deepEqual(avatars().map(avatar => avatar.props.style.background), initial);
    assert.match(source, /\.magi-wb-avatar \{[^}]*border-radius:10px/);
    assert.ok(!source.includes('BotIcon'));
  } finally { app.dispose(); }
});

test('Bot subtitles show independent latest previews and localized empty/unavailable states on refresh', async () => {
  let parentPreview = { state: 'message', text: 'Parent answer' };
  let childPreview = { state: 'message', text: 'Child\n answer' };
  const app = harness({
    fetchMain: async () => ({ ok: true, json: async () => ({ mode: 'parent', bot: { ...mainBot, preview: parentPreview } }) }),
    fetchView: async () => ({ ok: true, json: async () => ({ mode: 'parent', nodes: [{ ...running, preview: childPreview }] }) }),
  });
  const previews = () => elements(app.browser()).filter(element => element.props.className === 'magi-wb-node-preview').map(element => element.props.children[0]);
  try {
    await open(app);
    assert.deepEqual(previews(), ['Parent answer', 'Child answer']);
    assert.match(rows(app.browser())[0].props['aria-label'], /Online/);
    parentPreview = { state: 'empty' }; childPreview = { state: 'unavailable' };
    await app.poll();
    assert.deepEqual(previews(), ['No messages yet', 'Preview unavailable']);
    app.setLocale('zh');
    assert.deepEqual(previews(), ['暂无消息', '消息预览暂不可用']);
    childPreview = { state: 'message', text: 'New child answer' };
    await app.poll();
    assert.deepEqual(previews(), ['暂无消息', 'New child answer']);
    childPreview = { state: 'attachment' };
    await app.poll();
    assert.deepEqual(previews(), ['暂无消息', '[附件]']);
    assert.match(source, /\.magi-wb-node-preview \{[^}]*text-overflow:ellipsis;[^}]*white-space:nowrap/);
    assert.ok(!source.includes('Main conversation'));
  } finally { app.dispose(); }
});

for (const href of ['http://127.0.0.1:57684/', 'file:///Applications/DeepSeek%20Harness.app/Contents/Resources/app.asar/dsh/index.html']) {
  test(`native conversation selection reuses the exact fixed Session on ${new URL(href).protocol}`, async () => {
    const app = harness({ href });
    try {
      await tick(); assert.equal(app.active.size, 2); assert.equal(app.browser(), null); assert.equal(app.timers.size, 0);
      assert.equal(app.expandedSwitch(), null);
      const initialBrand = app.active.get('sidebar.brand.name');
      const tree = await open(app);
      assert.deepEqual(app.navigation, [{ session: mainBot.mainSessionId }]);
      assert.equal(app.active.get('sidebar.brand.name'), initialBrand, 'no brand/root/native conversation remount');
      assert.deepEqual([...app.active.keys()].sort(), ['shell.leading', 'sidebar', 'sidebar.brand.name', 'sidebar.footer.action']);
      assert.equal(app.active.get('sidebar').options.priority, -10);
      assert.equal(app.active.get('shell.leading').options.priority, -10);
      assert.equal(elements(tree).some(element => ['iframe', 'dialog', 'main'].includes(element.type)), false);
      assert.ok(text(app.brand()).includes('MAGI')); assert.ok(text(app.brand()).includes('BOT'));
      assert.equal(rows(tree)[0].props['aria-current'], 'true');
      assert.ok(app.calls.every(call => call.options.cache === 'no-store'));
    } finally { app.dispose(); }
    assert.equal(app.active.size, 0); assert.equal(app.timers.size, 0);
  });
}

test('switch back restores native browser; reopen retains previous EVA selection without replacing main', async () => {
  const app = harness();
  try {
    let tree = await open(app); rows(tree)[1].props.onClick(); tree = app.browser();
    assert.deepEqual(app.navigation.at(-1), { panel: 'eva:eva-000' });
    const count = app.navigation.length;
    app.switch().props.onClick();
    assert.equal(app.browser(), null); assert.equal(app.active.size, 2); assert.equal(app.timers.size, 0);
    assert.equal(app.navigation.length, count + 1); assert.deepEqual(app.navigation.at(-1), { panel: null }, 'closing returns to native main without changing the Session');
    assert.ok(text(app.brand()).includes('deepseek')); assert.ok(text(app.brand()).includes('HARNESS'));
    app.switch().props.onClick(); await tick(); tree = app.browser();
    assert.equal(rows(tree)[1].props['aria-current'], 'true');
    assert.deepEqual(app.navigation.at(-1), { panel: 'eva:eva-000' });
    assert.equal(app.navigation.filter(call => call.session).length, 1);
  } finally { app.dispose(); }
});

test('collapsed and brand switches are localized and stop native ancestor clicks', async () => {
  const app = harness();
  try {
    await tick(); app.setLocale('zh');
    const button = app.switch(); assert.equal(button.props['aria-label'], '切换到 MAGI 工作台');
    let prevented = false, stopped = false;
    button.props.onClick({ preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
    await tick(); assert.ok(prevented && stopped);
    const back = elements(app.brand()).find(element => element.type === 'button');
    assert.equal(back.props['aria-label'], '切换回 DSH'); back.props.onClick();
    assert.equal(app.browser(), null);
  } finally { app.dispose(); }
});

test('poll and search update only Bot browser, never renavigate or reassign hidden selection', async () => {
  const app = harness({ nodes: [running, { handle: 'eva-001', status: 'running' }], registeredEva: ['eva-000', 'eva-001'] });
  try {
    let tree = await open(app); rows(tree)[2].props.onClick(); tree = app.browser();
    const navigation = app.navigation.slice();
    search(tree).props.onChange({ target: { value: ' EVA-000 ' } }); tree = app.browser();
    assert.equal(rows(tree).length, 1); assert.equal(rows(tree)[0].props['aria-current'], undefined);
    await app.poll(); tree = app.browser(); assert.deepEqual(app.navigation, navigation);
    search(tree).props.onChange({ target: { value: 'absent' } }); tree = app.browser();
    assert.equal(rows(tree).length, 0); assert.ok(text(tree).includes('No matching Bots'));
    search(tree).props.onChange({ target: { value: '' } }); tree = app.browser();
    assert.equal(rows(tree)[2].props['aria-current'], 'true'); assert.deepEqual(app.navigation, navigation);
  } finally { app.dispose(); }
});

test('native open failure is a sidebar alert with explicit retry of the same fixed ID', async () => {
  const app = harness({ failMain: true });
  try {
    let tree = await open(app);
    assert.ok(elements(tree).some(element => element.props.role === 'alert'));
    assert.ok(!text(tree).includes('private native failure'), 'private native errors are not exposed');
    assert.ok(!text(tree).includes('Opening main conversation…'), 'no loading curtain obscures native content');
    app.setFailMain(false); assert.ok(retry(tree)); retry(tree).props.onClick(); tree = app.browser();
    assert.deepEqual(app.navigation, [{ session: mainBot.mainSessionId }, { session: mainBot.mainSessionId }]);
    assert.equal(elements(tree).some(element => element.props.role === 'alert'), false);
  } finally { app.dispose(); }
});

test('EVA registration race is recoverable only through explicit retry, not inventory polling', async () => {
  const app = harness({ registeredEva: [] });
  try {
    let tree = await open(app); rows(tree)[1].props.onClick(); tree = app.browser();
    assert.ok(elements(tree).some(element => element.props.role === 'alert'));
    const count = app.navigation.length; app.panels.add('eva:eva-000');
    await app.poll(); tree = app.browser(); assert.equal(app.navigation.length, count);
    assert.ok(retry(tree)); retry(tree).props.onClick(); tree = app.browser();
    assert.deepEqual(app.navigation.at(-1), { panel: 'eva:eva-000' });
    assert.equal(elements(tree).some(element => element.props.role === 'alert'), false);
  } finally { app.dispose(); }
});

test('inventory validates, deduplicates and sorts EVA rows, without trusting parent impersonation', async () => {
  const app = harness({ nodes: [{ handle: 'eva-002', status: 'stopped' }, null, { handle: '../bad' }, running, running, { ...running, handle: 'dsh-main', kind: 'parent' }] });
  try {
    const tree = await open(app);
    assert.deepEqual(rows(tree).map(row => row.props.key), ['parent:dsh-main', 'dsh-main', 'eva-000', 'eva-002']);
    rows(tree)[1].props.onClick();
    assert.deepEqual(app.navigation.at(-1), { panel: 'eva:dsh-main' }, 'an EVA called dsh-main is never the native parent');
  } finally { app.dispose(); }
});

for (const failure of ['absent', 'http', 'network']) {
  test(`fixed native Main Bot remains available without optional EVA inventory: ${failure}`, async () => {
    const app = harness({ fetchView() { if (failure === 'network') throw new Error('unreachable'); return { ok: false, status: failure === 'absent' ? 404 : 500 }; } });
    try { const tree = await open(app); assert.equal(rows(tree).length, 1); assert.deepEqual(app.navigation, [{ session: mainBot.mainSessionId }]); }
    finally { app.dispose(); }
  });
}

test('transient inventory failure retains known rows and native selection; refresh does not navigate', async () => {
  let available = true;
  const app = harness({ fetchView: () => available ? { ok: true, json: async () => ({ mode: 'parent', nodes: [running] }) } : { ok: false, status: 500 } });
  try {
    await open(app); const count = app.navigation.length; available = false;
    await app.poll(); const tree = app.browser();
    assert.equal(rows(tree).length, 2); assert.equal(app.snapshot.error, true); assert.equal(app.navigation.length, count);
    available = true; await app.poll(); app.browser(); assert.equal(app.snapshot.error, false);
  } finally { app.dispose(); }
});

test('embedded pages and child Hosts have no MAGI surfaces, navigation, or polling', async () => {
  for (const options of [{ embedded: true }, { mode: 'child' }]) {
    const app = harness(options);
    try { await tick(); assert.equal(app.active.size, 0); assert.equal(app.timers.size, 0); assert.deepEqual(app.navigation, []); assert.equal(app.calls.length, options.embedded ? 0 : 1); }
    finally { app.dispose(); }
  }
});

test('unload aborts pending inventory and ignores late surface installation', async () => {
  let release;
  const app = harness({ fetchView: () => new Promise(resolve => { release = resolve; }) });
  const signal = app.calls[0].options.signal; app.dispose(); assert.ok(signal.aborted);
  release({ ok: true, json: async () => ({ mode: 'parent', nodes: [running] }) }); await tick();
  assert.equal(app.active.size, 0); assert.equal(app.timers.size, 0); assert.deepEqual(app.navigation, []);
});


test('compact native sidebar props keep accessible contacts and expand before selecting', async () => {
  const app = harness();
  try {
    await open(app);
    const events = [];
    const tree = app.browser({ wide: false, expandSidebar() { events.push({ navigationCount: app.navigation.length }); } });
    assert.equal(tree.props['data-wide'], 'false');
    assert.ok(rows(tree).every(row => row.props['aria-label'] === row.props.title));
    const count = app.navigation.length;
    rows(tree)[1].props.onClick();
    assert.deepEqual(events, [{ navigationCount: count }], 'expand before changing native selection');
    assert.deepEqual(app.navigation.at(-1), { panel: 'eva:eva-000' });
    const wide = app.browser({ wide: true, expandSidebar() { assert.fail('Expanded owner does not need expansion'); } });
    assert.equal(wide.props['data-wide'], 'true');
    rows(wide)[0].props.onClick();
    assert.deepEqual(app.navigation.at(-1), { session: mainBot.mainSessionId });
    const css = elements(tree).filter(element => element.type === 'style').flatMap(element => element.props.children).join('');
    assert.match(css, /\[data-wide=false\].*\.magi-wb-search/);
    assert.match(css, /\.magi-wb-node-copy \{ display:none/);
  } finally { app.dispose(); }
});
