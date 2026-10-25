import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

for (const embedded of [true, false]) for (const hash of ['', '#eva-main']) {
  test(`child embedded=${embedded}, hash=${hash || '(cleared after login)'}`, async () => {
    let plugin, phase = 'loading', listener;
    const opened = [], sidebars = [], cleanup = [];
    const window = { __ModuleLoader__: { load({ factory }) { plugin = factory(() => ({})); } } };
    window.parent = embedded ? {} : window;
    runInNewContext(await readFile(new URL('../client.js', import.meta.url), 'utf8'), {
      window, location: { hash, pathname: '/', search: '' },
      history: { replaceState() {} }, AbortController, queueMicrotask,
      globalThis: { __DSH_TRANSPORT__: { fetch: async () => ({ ok: true, json: async () => ({ mode: 'child', mainSessionId: 'fixed-main' }) }) } },
      setTimeout() { assert.fail('Child must not poll parent EVA entries'); }, clearTimeout() {},
    });
    plugin.apply({
      effect(callback) { const dispose = callback(); if (dispose) cleanup.push(dispose); },
      locale: { register() { return () => {}; }, bind() { return (key) => key; } },
      slots: {
        inject(name, callback) { assert.equal(name, 'sidebar'); return callback(); },
        register(options, component) { sidebars.push({ options, component }); return () => {}; },
      },
      sessions: { list: { getSnapshot() { return { phase }; }, subscribe(callback) { listener = callback; return () => { listener = undefined; }; } } },
      uiWorkspace: { openSession(id) { opened.push(id); } },
    });
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(opened, [], 'wait for the native Session list');
    phase = 'ready'; listener?.();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(opened, embedded || hash ? ['fixed-main'] : []);
    assert.equal(sidebars.length, embedded ? 1 : 0, 'never hide a standalone child sidebar');
    if (embedded) {
      assert.equal(sidebars[0].options.name, 'sidebar');
      assert.equal(sidebars[0].options.priority, 100, 'explicitly outrank the shipped default sidebar');
      assert.equal(sidebars[0].component(), null);
    }
    for (const dispose of cleanup.reverse()) dispose();
  });
}
