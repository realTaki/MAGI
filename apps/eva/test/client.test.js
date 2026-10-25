import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

test('online and offline EVA entries persist, update status, and only online clicks open a child', async () => {
  let plugin, refresh, frameURL, effectInstalled = false, locale = 'en';
  let nodes = [
    { handle: 'eva-000', status: 'running', mainSessionId: 'main-0' },
    { handle: 'eva-001', status: 'not-started', workspace: '/test/.dsh-eva-001' },
  ];
  const active = new Map(), dictionaries = new Map(), cleanup = [], opens = [];
  const react = {
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
    useSyncExternalStore(_subscribe, getSnapshot) { return getSnapshot(); },
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
      inject(name, callback) { assert.ok(['sidebar.panellist', 'main'].includes(name)); return callback(); },
      register(options, component) {
        assert.ok(options.name, 'Slot registration requires name');
        const key = `${options.name}/${options.id ?? options.key}`;
        assert.equal(active.has(key), false);
        active.set(key, { options, component });
        return () => active.delete(key);
      },
    },
  });
  const entry = (handle) => active.get(`sidebar.panellist/eva:${handle}`);
  const page = () => active.get('main/eva:eva-001').component();
  await new Promise((resolve) => setImmediate(resolve));
  try {
    assert.equal(active.size, 4, 'on-disk offline nodes need no live main Session');
    assert.equal(active.has('sidebar.panellist/eva'), false, 'no management-page entry');
    assert.equal(entry('eva-000').options.label(), 'EVA-000 · Online');
    assert.equal(entry('eva-001').options.label(), 'EVA-001 · Offline');
    const offline = page();
    const section = offline.type(offline.props);
    assert.equal(section.type, 'section');
    assert.equal(section.props.children[1].props.children[0], 'Offline');
    assert.equal(opens.length, 0, 'offline click must neither open nor start a Host');
    const stable = active.get('main/eva:eva-000');
    await refresh();
    assert.equal(active.get('main/eva:eva-000'), stable, 'unchanged polling must not remount frames');

    nodes[1] = { ...nodes[1], status: 'running', mainSessionId: 'main-1' };
    await refresh();
    assert.equal(active.size, 4);
    assert.equal(entry('eva-001').options.label(), 'EVA-001 · Online');
    effectInstalled = false;
    const online = page();
    online.type(online.props);
    await new Promise((resolve) => setImmediate(resolve));
    const frame = online.type(online.props);
    assert.equal(frame.type, 'iframe');
    assert.equal(frame.props.src, 'http://127.0.0.1:4242/test-login#eva-main');
    assert.ok(!frame.props.sandbox.includes('allow-top-navigation'));
    assert.equal(opens.length, 1);

    for (const [status, label] of [['stopped', 'Offline'], ['starting', 'Starting'], ['stopping', 'Stopping'], ['failed', 'Failed']]) {
      nodes[1] = { ...nodes[1], status };
      await refresh();
      assert.equal(active.size, 4, `${status} must not remove the EVA entry`);
      assert.equal(entry('eva-001').options.label(), `EVA-001 · ${label}`);
      effectInstalled = false;
      const inactive = page();
      assert.equal(inactive.type(inactive.props).type, 'section', 'old frame URL cannot mask offline status');
      assert.equal(opens.length, 1, 'inactive entries never request navigation');
    }
    locale = 'zh';
    assert.equal(entry('eva-001').options.label(), 'EVA-001 · 异常退出', 'labels follow locale without re-registration');
    nodes = [nodes[0]]; // Only a removed inventory item is removed from the sidebar.
    await refresh();
    assert.equal(active.size, 2);
    assert.equal(entry('eva-001'), undefined);
  } finally {
    for (const dispose of cleanup.reverse()) dispose();
  }
  assert.equal(active.size, 0);
});
