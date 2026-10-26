import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

test('parent never registers legacy EVA sidebar rows; existing main panel addresses remain compatible', async () => {
  let plugin, refresh, frameURL, effectInstalled = false, locale = 'en';
  let nodes = [
    { handle: 'eva-000', status: 'running', mainSessionId: 'main-0' },
    { handle: 'eva-001', status: 'not-started', workspace: '/test/.dsh-eva-001' },
  ];
  const active = new Map(), dictionaries = new Map(), cleanup = [], opens = [];
  const react = {
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
    useSyncExternalStore(_subscribe, getSnapshot) { return getSnapshot(); },
    useRef() { return { current: null }; },
    useState(value) {
      if (typeof value === 'string') return [frameURL ?? value, (next) => { frameURL = next; }];
      return [value, () => {}];
    },
    useEffect(callback) {
      if (effectInstalled) return;
      effectInstalled = true;
      const dispose = callback(); if (dispose) cleanup.push(dispose);
    },
  };
  runInNewContext(await readFile(new URL('../client.js', import.meta.url), 'utf8'), {
    window: { __ModuleLoader__: { load({ factory }) { plugin = factory(() => react); } } },
    globalThis: {
      fetch() { throw new Error('Must use authenticated transport'); },
      __DSH_TRANSPORT__: { fetch: async (path, init) => {
        if (path === 'api/eva/view') return { ok: true, json: async () => ({ mode: 'parent', nodes }) };
        opens.push(path);
        assert.equal(path, 'api/eva/open?handle=eva-001');
        assert.equal(init.method, 'POST');
        return { ok: true, json: async () => ({ url: 'http://127.0.0.1:4242/test-login#eva-main' }) };
      } },
    },
    location: { assign() { assert.fail('Must not navigate the parent'); } },
    AbortController,
    setTimeout(callback) { refresh = callback; return 1; },
    clearTimeout() {},
  });
  plugin.apply({
    effect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
    locale: {
      getSnapshot() { return locale; },
      subscribe() { return () => {}; },
      register(_namespace, language, dict) { dictionaries.set(language, dict); return () => {}; },
      bind() { return (key) => dictionaries.get(locale)[key]; },
    },
    slots: {
      inject(name, callback) { assert.equal(name, 'main', 'must never register sidebar.panellist again'); return callback(); },
      register(options, component) {
        assert.equal(options.name, 'main');
        const key = `${options.name}/${options.key}`;
        assert.equal(active.has(key), false);
        active.set(key, { options, component });
        return () => active.delete(key);
      },
    },
  });
  const page = () => active.get('main/eva:eva-001').component();
  const panel = () => { const element = page(); return element.type(element.props); };
  await new Promise((resolve) => setImmediate(resolve));
  try {
    assert.equal(active.size, 2, 'compatibility routes only, no sidebar entries');
    const section = panel();
    assert.equal(section.type, 'section');
    assert.equal(section.props.children[1].props.children[0], 'Offline');
    assert.equal(opens.length, 0, 'offline route must neither open nor start a Host');
    const stable = active.get('main/eva:eva-000');
    await refresh();
    assert.equal(active.get('main/eva:eva-000'), stable, 'unchanged polling must not remount frames');

    nodes[1] = { ...nodes[1], status: 'running', mainSessionId: 'main-1' };
    await refresh();
    assert.equal(active.size, 2);
    effectInstalled = false;
    const online = page();
    const preparing = online.type(online.props);
    assert.equal(preparing.props.style.placeContent, 'center', 'URL preparation shares the centered loading layout');
    assert.equal(preparing.props.children[0].props.children[0], 'Opening main conversation…');
    await new Promise((resolve) => setImmediate(resolve));
    const loadingFrame = online.type(online.props);
    const container = loadingFrame.type(loadingFrame.props);
    assert.equal(container.props['aria-busy'], true);
    const frame = container.props.children[0];
    assert.equal(frame.type, 'iframe');
    assert.equal(frame.props.style.opacity, 0, 'do not expose the initial native page');
    assert.equal(frame.props.src, 'http://127.0.0.1:4242/test-login#eva-main');
    assert.ok(!frame.props.sandbox.includes('allow-top-navigation'));
    assert.equal(opens.length, 1);

    for (const [status, label] of [['stopped', 'Offline'], ['starting', 'Starting'], ['stopping', 'Stopping'], ['failed', 'Failed']]) {
      nodes[1] = { ...nodes[1], status };
      await refresh();
      assert.equal(active.size, 2, `${status} retains only compatibility routes`);
      effectInstalled = false;
      const inactive = panel();
      assert.equal(inactive.type, 'section', 'old frame URL cannot mask offline status');
      assert.equal(inactive.props.children[1].props.children[0], label);
      assert.equal(opens.length, 1, 'inactive routes never request navigation');
    }
    locale = 'zh';
    assert.equal(panel().props.children[1].props.children[0], '异常退出');
    nodes = [nodes[0]];
    await refresh();
    assert.equal(active.size, 1);
    assert.equal(active.has('main/eva:eva-001'), false);
  } finally {
    for (const dispose of cleanup.reverse()) dispose();
  }
  assert.equal(active.size, 0);
});
