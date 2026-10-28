import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

async function fixture() {
  let plugin, refresh, locale = 'zh', notices = 0;
  let nodes = [{ handle: 'eva-001', status: 'not-started', workspace: '/test/.dsh-eva-001' }];
  const active = new Map(), dictionaries = new Map(), cleanup = [], requests = [], pending = [];
  const react = {
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
    useSyncExternalStore(subscribe, snapshot) { cleanup.push(subscribe(() => { notices++; })); return snapshot(); },
    useState(value) { return [value, () => {}]; },
    useRef() { return { current: null }; },
    useLayoutEffect() {},
    useEffect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
  };
  runInNewContext(await readFile(new URL('../client.js', import.meta.url), 'utf8'), {
    window: { __ModuleLoader__: { load({ factory }) { plugin = factory(() => react); } } },
    globalThis: {
      fetch() { assert.fail('Must use authenticated transport'); },
      __DSH_TRANSPORT__: { fetch(path, init) {
        if (path === 'api/eva/view') return Promise.resolve({ ok: true, json: async () => ({ mode: 'parent', nodes }) });
        assert.equal(path, 'api/eva/wake?handle=eva-001', 'offline render must never request child navigation');
        assert.equal(init.method, 'POST');
        requests.push({ path, init });
        return new Promise((resolve, reject) => pending.push({ resolve, reject }));
      } },
    },
    AbortController,
    setTimeout(callback) { refresh = callback; return 1; },
    clearTimeout() {},
  });
  plugin.apply({
    effect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
    locale: {
      register(_namespace, language, dict) { dictionaries.set(language, dict); return () => {}; },
      bind() { return (key) => dictionaries.get(locale)[key]; },
      getSnapshot() { return locale; }, subscribe() { return () => {}; },
    },
    slots: {
      inject(_name, callback) { return callback(); },
      register(options, component) {
        const key = `${options.name}/${options.id ?? options.key}`;
        active.set(key, component);
        return () => active.delete(key);
      },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  const page = () => active.get('main/eva:eva-001')();
  return {
    requests, pending, active,
    render() { const anchor = page(); const element = anchor.type(anchor.props); return element.type(element.props); },
    button() { return this.render().props.children.find((child) => child?.type === 'button'); },
    alert() { return this.render().props.children.find((child) => child?.props?.role === 'alert'); },
    async status(status) { nodes = [{ ...nodes[0], status }]; await refresh(); },
    language(value) { locale = value; },
    notices: () => notices,
    dispose() { for (const dispose of cleanup.splice(0).reverse()) dispose?.(); },
  };
}

test('explicit wake is localized, deduplicated and survives status-panel remounts', async () => {
  const ui = await fixture();
  try {
    const button = ui.button();
    assert.equal(button.props.children[0], '唤醒 EVA');
    assert.equal(button.props.type, 'button');
    assert.equal(button.props.disabled, false);
    assert.equal(ui.requests.length, 0, 'opening an offline panel must not start it');
    const request = button.props.onClick();
    await button.props.onClick(); // A stale click callback cannot issue a second request.
    assert.equal(ui.requests.length, 1);
    assert.equal(ui.button().props.disabled, true);
    assert.equal(ui.button().props['aria-busy'], true);
    assert.equal(ui.button().props.children[0], '唤醒中…');
    assert.ok(ui.notices() > 0, 'subscribed panels receive wake state changes');
    await ui.status('starting');
    assert.equal(ui.button().props.disabled, true, 'polling remount retains the in-flight action');
    assert.equal(ui.requests[0].init.signal.aborted, false, 'polling must not cancel startup');
    ui.pending.shift().resolve({ ok: true });
    await request;
    assert.equal(ui.button(), undefined, 'starting nodes without an in-flight request have no wake action');
    await ui.status('stopped');
    assert.equal(ui.button().props.disabled, false);
    ui.language('en');
    assert.equal(ui.button().props.children[0], 'Wake EVA');
    await ui.status('stopping');
    assert.equal(ui.button(), undefined, 'stopping nodes cannot be woken');
  } finally { ui.dispose(); }
  assert.equal(ui.active.size, 0);
});

test('wake failures remain visible after polling remount and allow explicit retry', async () => {
  const ui = await fixture();
  try {
    let request = ui.button().props.onClick();
    await ui.status('starting');
    ui.pending.shift().resolve({ ok: false, json: async () => ({ code: 'start-failed', error: 'SECRET-CHILD-URL' }) });
    await request;
    await ui.status('failed');
    assert.match(ui.alert().props.children[0], /无法唤醒/);
    assert.ok(!ui.alert().props.children[0].includes('SECRET'));
    assert.equal(ui.button().props.disabled, false);
    request = ui.button().props.onClick();
    assert.equal(ui.alert(), undefined, 'retry clears the previous failure');
    ui.pending.shift().resolve({ ok: false, json: async () => ({ code: 'forbidden' }) });
    await request;
    assert.match(ui.alert().props.children[0], /无法确认浏览器身份/);
    request = ui.button().props.onClick();
    ui.pending.shift().reject(new Error('network unavailable'));
    await request;
    assert.match(ui.alert().props.children[0], /无法唤醒/);
    assert.equal(ui.requests.length, 3);
  } finally { ui.dispose(); }
});

test('plugin disposal aborts its wake carrier without publishing late results', async () => {
  const ui = await fixture();
  const request = ui.button().props.onClick();
  ui.dispose();
  assert.equal(ui.requests[0].init.signal.aborted, true);
  const notices = ui.notices();
  ui.pending.shift().reject(new Error('cancelled'));
  await request;
  assert.equal(ui.notices(), notices);
  assert.equal(ui.active.size, 0);
});
