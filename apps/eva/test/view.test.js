import test from 'node:test';
import assert from 'node:assert/strict';
import { registerParentView, registerChildView, VIEW_PATH, OPEN_PATH } from '../view.js';

function fixture() {
  const routes = new Map();
  const ctx = { connection: { fetch: { register(route) {
    assert.equal(routes.has(route.path), false);
    routes.set(route.path, route);
  } } } };
  const request = (path, method = 'GET') => {
    const url = new URL(path, 'http://127.0.0.1:1234');
    const route = routes.get(url.pathname);
    assert.ok(route, 'route registered through authenticated Connection');
    assert.ok(route.methods.includes(method));
    return route.fetch(new Request(url, { method }));
  };
  return { ctx, routes, request };
}
function privateResponse(response) {
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
}

const secret = 'child-private-login-token';
const launch = `http://127.0.0.1:2345/?token=${secret}#eva-main`;

test('parent registers only authenticated Connection view/open routes with explicit methods', () => {
  const { ctx, routes } = fixture();
  registerParentView(ctx, {});
  assert.deepEqual([...routes.keys()], [VIEW_PATH, OPEN_PATH]);
  assert.deepEqual(routes.get(VIEW_PATH).methods, ['GET']);
  assert.deepEqual(routes.get(OPEN_PATH).methods, ['GET', 'POST']);
  for (const route of routes.values()) {
    assert.equal(route.requestBody, 'buffered');
    assert.equal(route.access, undefined, 'do not opt out of Connection owner-only default');
  }
});

test('parent view projects list fields only and never asks for a child launch URL', async () => {
  const { ctx, request } = fixture();
  let launchCalls = 0;
  registerParentView(ctx, {
    async status() { return { nodes: [{ handle: 'eva-000', status: 'running', workspace: '/private/workspace',
      mainSessionId: 'session-main', launchUrl: launch, token: secret, diagnostics: secret, profileDir: '/private/profile' }] }; },
    launchUrl() { launchCalls++; return launch; },
  });
  const response = await request(VIEW_PATH);
  privateResponse(response);
  assert.equal(response.status, 200);
  const text = await response.text();
  assert.ok(!text.includes(secret));
  assert.ok(!text.includes('launchUrl'));
  assert.deepEqual(JSON.parse(text), { mode: 'parent', nodes: [{ handle: 'eva-000', status: 'running',
    workspace: '/private/workspace', mainSessionId: 'session-main' }] });
  assert.equal(launchCalls, 0);
});

for (const method of ['GET', 'POST']) {
  test(`explicit ${method} open returns child navigation only when requested`, async () => {
    const { ctx, request } = fixture();
    const calls = [];
    registerParentView(ctx, {
      async status() { return { nodes: [] }; },
      launchUrl(handle) { calls.push(handle); return launch; },
    });
    await request(VIEW_PATH);
    assert.deepEqual(calls, []);
    const response = await request(`${OPEN_PATH}?handle=eva-000`, method);
    privateResponse(response);
    assert.deepEqual(calls, ['eva-000']);
    if (method === 'GET') {
      assert.equal(response.status, 303);
      assert.equal(response.headers.get('location'), launch);
      assert.equal(await response.text(), '');
    } else {
      assert.equal(response.status, 200);
      assert.equal(response.headers.get('location'), null);
      assert.deepEqual(await response.json(), { url: launch.replace('/?token=', '/eva/embed?token=') });
    }
  });
}

for (const method of ['GET', 'POST']) {
  test(`${method} open returns 409 for stopped/unknown valid handles and 404 for invalid handles without error leaks`, async () => {
    const { ctx, request } = fixture();
    registerParentView(ctx, { launchUrl(handle) {
      if (!handle || !/^[a-z][a-z0-9-]{0,31}$/.test(handle)) throw new Error(secret);
      return undefined;
    } });
    for (const handle of ['eva-stopped', 'eva-unknown']) {
      const response = await request(`${OPEN_PATH}?handle=${handle}`, method);
      privateResponse(response);
      assert.equal(response.status, 409);
      assert.equal(response.headers.get('location'), null);
      assert.ok(!(await response.text()).includes(secret));
    }
    for (const query of ['', '?handle=../escape', '?handle=UPPER', '?handle=']) {
      const response = await request(`${OPEN_PATH}${query}`, method);
      privateResponse(response);
      assert.equal(response.status, 404);
      assert.ok(!(await response.text()).includes(secret));
    }
  });
}

test('child view returns 503 until ready, then only main Session identity', async () => {
  const { ctx, request, routes } = fixture();
  let mainSessionId;
  registerChildView(ctx, () => mainSessionId);
  assert.deepEqual([...routes.keys()], [VIEW_PATH]);
  assert.deepEqual(routes.get(VIEW_PATH).methods, ['GET']);
  const loading = await request(VIEW_PATH);
  privateResponse(loading);
  assert.equal(loading.status, 503);
  assert.match(await loading.text(), /initializing/);
  mainSessionId = 'session-persistent-main';
  const ready = await request(VIEW_PATH);
  privateResponse(ready);
  assert.equal(ready.status, 200);
  assert.deepEqual(await ready.json(), { mode: 'child', mainSessionId });
});
