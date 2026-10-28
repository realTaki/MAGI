import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

test('parent registers stable compatibility main routes plus one retained deck, never legacy sidebar rows', async () => {
  let plugin, refresh, locale = 'en';
  let nodes = [
    { handle: 'eva-000', status: 'running', mainSessionId: 'main-0' },
    { handle: 'eva-001', status: 'not-started', workspace: '/test/.dsh-eva-001' },
  ];
  const active = new Map(), dictionaries = new Map(), cleanup = [], opens = [];
  const react = {
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
    useSyncExternalStore(_subscribe, getSnapshot) { return getSnapshot(); },
    useRef() { return { current: null }; },
    useState(value) { return [value, () => {}]; },
    useLayoutEffect() {},
    useEffect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
  };
  runInNewContext(await readFile(new URL('../client.js', import.meta.url), 'utf8'), {
    window: { __ModuleLoader__: { load({ factory }) { plugin = factory(() => react); } } },
    globalThis: {
      fetch() { throw new Error('Must use authenticated transport'); },
      __DSH_TRANSPORT__: { fetch: async (path) => {
        if (path === 'api/eva/view') return { ok: true, json: async () => ({ mode: 'parent', nodes }) };
        opens.push(path); assert.fail('An offline route or unvisited online node must not authenticate a frame');
      } },
    },
    AbortController,
    setTimeout(callback) { refresh = callback; return 1; }, clearTimeout() {},
  });
  plugin.apply({
    effect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
    locale: {
      getSnapshot() { return locale; }, subscribe() { return () => {}; },
      register(_namespace, language, dict) { dictionaries.set(language, dict); return () => {}; },
      bind() { return (key) => dictionaries.get(locale)[key]; },
    },
    slots: {
      inject(name, callback) { assert.ok(['main', 'shell.overlay'].includes(name)); return callback(); },
      register(options, component) {
        assert.ok(['main', 'shell.overlay'].includes(options.name));
        const key = `${options.name}/${options.key ?? options.id}`;
        assert.equal(active.has(key), false);
        active.set(key, { options, component }); return () => active.delete(key);
      },
    },
  });
  const panel = () => {
    const anchor = active.get('main/eva:eva-001').component();
    const main = anchor.type(anchor.props); return main.type(main.props);
  };
  await new Promise((resolve) => setImmediate(resolve));
  try {
    assert.equal(active.size, 3, 'two compatibility routes and one persistent overlay');
    const stable = active.get('main/eva:eva-001');
    const deck = active.get('shell.overlay/eva:retained-frames');
    assert.equal(panel().props.children[1].props.children[0], 'Offline');
    await refresh();
    assert.equal(active.get('main/eva:eva-001'), stable);
    for (const [status, label] of [['stopped', 'Offline'], ['starting', 'Starting'], ['stopping', 'Stopping'], ['failed', 'Failed']]) {
      nodes[1] = { ...nodes[1], status }; await refresh();
      assert.equal(active.get('main/eva:eva-001'), stable, 'state updates do not unregister the native main owner');
      assert.equal(panel().props.children[1].props.children[0], label);
    }
    locale = 'zh'; assert.equal(panel().props.children[1].props.children[0], '异常退出');
    assert.equal(active.get('shell.overlay/eva:retained-frames'), deck);
    assert.equal(opens.length, 0);
    nodes = [nodes[0]]; await refresh();
    assert.equal(active.size, 2); assert.equal(active.has('main/eva:eva-001'), false);
  } finally { for (const dispose of cleanup.reverse()) dispose(); }
  assert.equal(active.size, 0);
});
