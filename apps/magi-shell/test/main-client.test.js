import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

const source = await readFile(new URL('../client.js', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

// Contract regressions for removing the separate Main Bot renderer. The native
// conversation stays owned by DSH; these are not browser/visual assertions.
function load({ embedded = true, href = 'http://127.0.0.1:57684/', hash = '', search = '' } = {}) {
  let plugin;
  const React = { createElement() {}, Fragment: Symbol('fragment') };
  const window = { __ModuleLoader__: { load({ factory }) { plugin = factory(name => { assert.equal(name, 'react'); return React; }); } } };
  window.parent = embedded ? {} : window;
  runInNewContext(source, {
    window, location: { href, hash, search }, URL, URLSearchParams, AbortController,
    setTimeout() { assert.fail('Embedded/closed Main Bot must never start a readiness loop'); }, clearTimeout() {},
    globalThis: { fetch() { assert.fail('Never use a separate Main Bot HTTP renderer'); } },
  });
  return plugin;
}

for (const href of ['http://127.0.0.1:57684/', 'file:///Applications/DeepSeek%20Harness.app/Contents/Resources/app.asar/dsh/index.html']) {
  for (const marker of [{}, { hash: '#eva-parent-main' }, { hash: '#eva-main' }, { search: '?magi-main=1' }]) {
    test(`embedded Main Bot markers are inert, not a second conversation renderer: ${href} ${JSON.stringify(marker)}`, () => {
      const plugin = load({ href, ...marker });
      const forbidden = new Proxy({}, { get(_target, key) { assert.fail(`Embedded page must not access native services: ${String(key)}`); } });
      plugin.apply(forbidden);
    });
  }
}

test('Client dependency contract is native slots, locale, workspace navigation and layout only', () => {
  const plugin = load();
  assert.deepEqual(Array.from(plugin.inject).sort(), ['layout', 'locale', 'slots', 'uiWorkspace']);
});

test('Main Bot has no iframe, main-open transport, handshake, or catalog readiness adapter', () => {
  assert.doesNotMatch(source, /\bh\(\s*['"]iframe['"]/);
  assert.doesNotMatch(source, /api\/magi-shell\/main-open|eva:main-state|postMessage\s*\(/);
  assert.doesNotMatch(source, /requestAnimationFrame|cancelAnimationFrame|openState|retainInfo/);
  assert.doesNotMatch(source, /ctx\.(?:sessions|workspaces)\b/);
  assert.doesNotMatch(source, /ctx\.uiWorkspace\.(?:startSession|openWorkspace|connectWorkspace)\s*\(/);
  assert.match(source, /ctx\.uiWorkspace\.openSession\(/);
  assert.match(source, /ctx\.layout\.selectPanel\(/);
});

test('native root inventory does not navigate, read catalogs, or register a Main Bot frame while closed', async () => {
  const plugin = load({ embedded: false, search: '?magi-main=1' });
  const calls = [], entries = [], cleanup = [];
  const forbidden = new Proxy({}, { get(_target, key) { assert.fail(`Closed page must not use conversation/catalog APIs: ${String(key)}`); } });
  // Re-load with an authenticated inventory carrier; file/HTTP location is not
  // used to derive a new conversation URL.
  let authenticated;
  const window = { __ModuleLoader__: { load({ factory }) { authenticated = factory(() => ({ createElement() {} })); } } }; window.parent = window;
  runInNewContext(source, {
    window, URL, URLSearchParams, location: { href: 'file:///native/index.html', search: '?magi-main=1', hash: '' }, AbortController,
    setTimeout() { assert.fail('Closed view does not poll'); }, clearTimeout() {},
    globalThis: { __DSH_TRANSPORT__: { async fetch(path, init) {
      calls.push({ path, init });
      if (path === 'api/eva/view') return { ok: true, json: async () => ({ mode: 'parent', nodes: [] }) };
      assert.equal(path, 'api/eva/parent-main');
      return { ok: true, json: async () => ({ mode: 'parent', bot: { kind: 'parent', handle: 'dsh-main', status: 'running', mainSessionId: 'exact-fixed-id', workspace: '/parent' } }) };
    } } },
  });
  assert.ok(plugin);
  authenticated.apply({
    effect(callback) { const off = callback(); if (off) cleanup.push(off); },
    locale: { register() { return () => {}; }, bind() { return key => key; } },
    slots: {
      inject(name, callback) { assert.ok(['sidebar.brand.name', 'sidebar.footer.action'].includes(name)); const off = callback(); if (off) cleanup.push(off); },
      register(options) { entries.push(options); return () => {}; },
    },
    sessions: forbidden, workspaces: forbidden, uiWorkspace: forbidden, layout: forbidden,
  });
  try {
    await tick();
    assert.deepEqual(calls.map(call => call.path), ['api/eva/view', 'api/eva/parent-main']);
    assert.deepEqual(entries.map(entry => entry.name).sort(), ['sidebar.brand.name', 'sidebar.footer.action']);
    assert.ok(entries.every(entry => !entry.id?.includes('frame')));
  } finally { for (const off of cleanup.reverse()) off(); }
});
