import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../client.js', import.meta.url), 'utf8');
function load(react = {}) {
  let plugin;
  vm.runInNewContext(source, { window: { __ModuleLoader__: { load(definition) { plugin = definition.factory(() => react); } } }, AbortController });
  return plugin;
}
const ok = value => ({ ok: true, value });
const empty = () => Promise.resolve(ok({ items: [], hasMore: false }));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function harness(search = empty, list = empty) {
  const states = [], calls = [];
  const remote = { session: {
    search(...args) { calls.push(args); return search(...args); }, list,
  } };
  return { states, calls, reader: load().createReader(remote, state => states.push(state)) };
}

test('trimmed literal query uses only current injected native Remote and preserves body snippet', async () => {
  const excerpt = '<img src=x onerror=evil()> 正文';
  const x = harness(async () => ok({ items: [{ sessionId: 'old-session', snippet: excerpt }], hasMore: false }),
    async () => ok({ items: [{ sessionId: 'old-session', updatedAt: 123 }] }));
  await x.reader.search('  正文  ');
  assert.equal(x.calls.length, 1);
  assert.equal(x.calls[0][0].query, '正文');
  assert.ok(x.calls[0][1] instanceof AbortSignal);
  assert.equal(x.states[0].status, 'loading');
  assert.equal(x.states.at(-1).items[0].snippet, excerpt);
  assert.equal(x.states.at(-1).items[0].updatedAt, 123);
});

test('blank input clears and cancels without native reads', async () => {
  const x = harness(); await x.reader.search('  ');
  assert.equal(x.calls.length, 0); assert.equal(x.states.at(-1).status, 'idle');
});

test('empty success and native business errors are distinct states', async () => {
  const x = harness(); await x.reader.search('none');
  assert.equal(x.states.at(-1).status, 'done'); assert.equal(x.states.at(-1).items.length, 0);
  const y = harness(async () => ({ ok: false, error: { message: 'denied' } }));
  await y.reader.search('x'); assert.equal(y.states.at(-1).status, 'error');
  const z = harness(async () => { throw new Error('offline'); });
  await z.reader.search('x'); assert.equal(z.states.at(-1).status, 'error');
});

test('metadata failure does not hide content hits or invent timestamps', async () => {
  const x = harness(async () => ok({ items: [{ sessionId: 's', snippet: 'hit' }], hasMore: false }),
    async () => ({ ok: false }));
  await x.reader.search('hit');
  assert.equal(x.states.at(-1).status, 'done'); assert.equal(x.states.at(-1).items[0].updatedAt, undefined);
});

test('superseded search aborts and late old response cannot replace new hits', async () => {
  const old = deferred(); const x = harness(query => query.query === 'old' ? old.promise : Promise.resolve(ok({ items: [], hasMore: false })));
  const pending = x.reader.search('old'); await x.reader.search('new');
  assert.equal(x.calls[0][1].aborted, true);
  old.resolve(ok({ items: [{ sessionId: 'stale' }], hasMore: false })); await pending;
  assert.equal(x.states.at(-1).items.length, 0);
});

test('tab disposal aborts and ignores late replies', async () => {
  const delayed = deferred(); const x = harness(() => delayed.promise);
  const pending = x.reader.search('x'); const count = x.states.length; x.reader.dispose();
  assert.equal(x.calls[0][1].aborted, true); delayed.resolve(ok({ items: [], hasMore: false })); await pending;
  assert.equal(x.states.length, count);
});

test('native cap and hasMore are session-based and never silently truncated', async () => {
  for (const [count, more] of [[20, true], [21, false], [1, false]]) {
    const x = harness(async () => ok({ items: Array.from({ length: count }, (_, n) => ({ sessionId: String(n), snippet: 'hit' })), hasMore: more }));
    await x.reader.search('hit'); assert.equal(x.states.at(-1).items.length, Math.min(count, 20));
    assert.equal(x.states.at(-1).hasMore, more || count > 20);
  }
});

test('owned right tab/guide/body register and release through public effects', () => {
  const effects = [], registrations = [], slots = [], dictionaries = [];
  const plugin = load();
  plugin.apply({
    effect(fn) { effects.push(fn()); },
    locale: { register(ns, copy) { dictionaries.push(copy); return () => {}; }, bind() { return key => dictionaries[0].zh[key]; } },
    sidebarRightTabs: { register(def) { registrations.push(def); return () => registrations.pop(); } },
    slots: { inject(name, fn) { assert.equal(name, 'sidebar.right.pane.tab'); return fn(); }, register(def, body) { slots.push({ def, body }); return () => slots.pop(); } },
  });
  assert.equal(registrations[0].title(), '搜索聊天内容');
  assert.equal(registrations[0].guide[0].title(), '搜索聊天内容');
  assert.equal(slots[0].def.key, registrations[0].id);
  assert.deepEqual(Array.from(plugin.inject), ['slots', 'locale', 'sidebarRightTabs', 'remote.session']);
  effects.reverse().forEach(fn => fn()); assert.equal(slots.length, 0); assert.equal(registrations.length, 0);
});

test('rendered snippets stay text children and UI discloses scope/cap/time limitations', async () => {
  let stateIndex = 0, body, texts;
  const snippet = '<script>bad()</script>';
  const React = {
    createElement: (type, props, ...children) => ({ type, props, children }),
    useState: initial => [stateIndex++ === 0 ? 'query' : { status: 'done', hasMore: true, items: [{ sessionId: 's', snippet, updatedAt: 123 }] }, () => {}],
    useRef: () => ({ current: undefined }), useEffect() {},
  };
  load(React).apply({ effect: fn => fn(), remote: {},
    locale: { register(ns, copy) { texts = copy.zh; }, bind: () => key => texts[key] }, sidebarRightTabs: { register() {} },
    slots: { inject: (name, fn) => fn(), register(def, component) { body = component; } },
  });
  const tree = body(); const nodes = [];
  function visit(value) { if (Array.isArray(value)) return value.forEach(visit); if (value && typeof value === 'object') { nodes.push(value); visit(value.children); } }
  visit(tree);
  assert.equal(nodes.find(n => n.type === 'blockquote').children[0], snippet);
  assert.equal(nodes.some(n => n.props?.dangerouslySetInnerHTML), false);
  const json = JSON.stringify(tree);
  for (const text of ['20 个会话', '不是 20 条消息', '内部会话', '不是命中消息时间', '仍有更多']) assert.ok(json.includes(text), text);
});
