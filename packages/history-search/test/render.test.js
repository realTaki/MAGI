import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import TestRenderer, { act } from 'react-test-renderer';

const source = await readFile(new URL('../client.js', import.meta.url), 'utf8');
function mountPlugin(remote = { session: {} }) {
  let plugin;
  vm.runInNewContext(source, { AbortController, window: { __ModuleLoader__: {
    load(definition) { plugin = definition.factory(name => { assert.equal(name, 'react'); return React; }); },
  } } });
  const types = [], entries = [], cleanup = [];
  let dictionary;
  plugin.apply({ get remote() { return typeof remote === 'function' ? remote() : remote; }, effect(fn) { const dispose = fn(); if (dispose) cleanup.push(dispose); },
    locale: { register(ns, copy) { dictionary = copy.zh; return () => {}; }, bind() { return key => dictionary[key]; } },
    sidebarRightTabs: { register(type) { types.push(type); return () => types.splice(types.indexOf(type), 1); } },
    slots: { inject(name, callback) { assert.equal(name, 'sidebar.right.pane.tab'); return callback(); },
      register(options, component) { const entry = { options, component }; entries.push(entry); return () => entries.splice(entries.indexOf(entry), 1); } },
  });
  // rc.5 sidebar registry projects definition.kind into guide rows; GuideBody
  // opens selected.kind. TabSlot resolves the definition then uses its id,
  // not the guide entry id or kind, as the keyed body dispatch value.
  const selected = { ...types[0].guide[0], kind: types[0].kind, providerId: types[0].id };
  const tab = { kind: selected.kind };
  const definition = types.find(type => type.kind === tab.kind);
  const entry = entries.find(entry => entry.options.key === (definition?.id ?? tab.kind));
  assert.ok(entry, 'guide selection must resolve an owned body entry');
  return { Body: entry.component, dispose() { cleanup.reverse().forEach(fn => fn()); assert.equal(entries.length, 0); assert.equal(types.length, 0); } };
}

test('real React SSR of guide-selected body shows initial controls without RPC', () => {
  const { Body, dispose } = mountPlugin({ get session() { throw new Error('no RPC on first paint'); } });
  const html = renderToStaticMarkup(React.createElement(Body));
  assert.match(html, /type="search"/);
  assert.match(html, /搜索聊天内容/);
  assert.match(html, /20 个会话/);
  assert.match(html, /本 Host/);
  dispose();
});

test('mount does not acquire transport; unavailable transport stays recoverable without retiring the entry', async () => {
  let available = false, acquisitions = 0;
  const { Body, dispose } = mountPlugin(() => {
    acquisitions++;
    if (!available) throw new Error('transport not available');
    return { session: {
      search: async () => ({ ok: true, value: { items: [{ sessionId: 's', snippet: 'recovered result' }], hasMore: false } }),
      list: async () => ({ ok: true, value: { items: [] } }),
    } };
  });
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(Body)); });
  assert.equal(acquisitions, 0);
  await act(async () => { view.root.findByType('input').props.onChange({ target: { value: '正文' } }); });
  await act(async () => { view.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
  assert.match(JSON.stringify(view.toJSON()), /搜索失败/);
  assert.equal(view.root.findAllByType('input').length, 1);
  available = true;
  await act(async () => { view.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
  assert.match(JSON.stringify(view.toJSON()), /recovered result/);
  await act(async () => { view.unmount(); });
  dispose();
});

test('real React mount executes hook initialization and renders search failures visibly', async () => {
  const calls = [];
  const { Body, dispose } = mountPlugin({ session: {
    search(input, signal) { calls.push({ input, signal }); return Promise.reject(new Error('offline')); },
    list() { return Promise.reject(new Error('offline')); },
  } });
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(Body)); });
  assert.equal(calls.length, 0);
  await act(async () => { view.root.findByType('input').props.onChange({ target: { value: '正文' } }); });
  await act(async () => { view.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
  assert.equal(calls.length, 1);
  assert.match(JSON.stringify(view.toJSON()), /搜索失败/);
  assert.equal(view.root.findAllByType('input').length, 1);
  await act(async () => { view.unmount(); });
  dispose();
});
