import test from 'node:test';
import assert from 'node:assert/strict';
import { registerParentView, registerChildView, VIEW_PATH, OPEN_PATH, WAKE_PATH } from '../view.js';

function fixture() {
  const routes = new Map();
  const browserPeer = { principal: { kind: 'owner' } };
  const apiPeer = { principal: { kind: 'owner' } };
  const admissions = [];
  const ctx = { connection: { browserPeer, admit(request) {
    admissions.push(request);
    if (request.headers.cookie === 'native-browser-cookie') return { peer: browserPeer };
    if (request.headers.cookie === 'native-api-cookie') return { peer: apiPeer };
    return { rejection: 401 };
  }, fetch: { register(route) {
    assert.equal(routes.has(route.path), false);
    routes.set(route.path, route);
  } } } };
  const request = (path, method = 'GET', options = {}) => {
    const url = new URL(path, 'http://127.0.0.1:1234');
    const route = routes.get(url.pathname);
    assert.ok(route, 'route registered through authenticated Connection');
    assert.ok(route.methods.includes(method));
    return route.fetch(new Request(url, { method, headers: { host: url.host,
      cookie: 'native-browser-cookie', ...options.headers }, signal: options.signal }));
  };
  return { ctx, routes, request, admissions };
}
function privateResponse(response) {
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
}

const secret = 'child-private-login-token';
const launch = `http://127.0.0.1:2345/?token=${secret}#eva-main`;

test('parent registers authenticated Connection routes with POST-only wake', () => {
  const { ctx, routes } = fixture();
  registerParentView(ctx, {});
  assert.deepEqual([...routes.keys()], [WAKE_PATH, VIEW_PATH, OPEN_PATH]);
  assert.deepEqual(routes.get(WAKE_PATH).methods, ['POST']);
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

test('wake starts the explicit known handle and projects safe fields without launch credentials', async () => {
  const { ctx, request, admissions } = fixture();
  const calls = [];
  const safe = { handle: 'eva-000', status: 'running', workspace: '/known/home', mainSessionId: 'session-main' };
  registerParentView(ctx, {
    async status(...args) { assert.deepEqual(args, []); return { nodes: [{ handle: safe.handle, status: 'not-started' }] }; },
    async start(...args) { calls.push(args); return { ...safe, launchUrl: launch, token: secret, diagnostics: secret, pid: 123 }; },
    launchUrl() { assert.fail('wake is not navigation'); },
  });
  const response = await request(`${WAKE_PATH}?handle=eva-000`, 'POST');
  privateResponse(response);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('location'), null);
  assert.deepEqual(await response.json(), safe);
  assert.deepEqual(calls, [[undefined, 'eva-000']]);
  assert.equal(admissions[0].headers.cookie, 'native-browser-cookie');
  assert.equal(admissions[0].method, 'POST');
});

test('wake denies API owner peers, agent credentials and credential-less desktop before reading inventory', async () => {
  const { ctx, request } = fixture();
  registerParentView(ctx, {
    status() { assert.fail('forbidden request must not read inventory'); },
    start() { assert.fail('forbidden request must not start'); },
  });
  for (const headers of [{ cookie: 'native-api-cookie' },
    { cookie: '', authorization: `Bearer ${secret}` }, { cookie: '', 'x-browser-peer': 'true' }]) {
    const response = await request(`${WAKE_PATH}?handle=eva-000`, 'POST', { headers });
    privateResponse(response);
    assert.equal(response.status, 403);
    const body = await response.json();
    assert.equal(body.code, 'forbidden');
    assert.ok(!JSON.stringify(body).includes(secret));
  }
  ctx.connection.admit = () => { throw new Error(secret); };
  assert.equal((await request(`${WAKE_PATH}?handle=eva-000`, 'POST')).status, 403);
  ctx.connection.admit = () => ({});
  ctx.connection.browserPeer = undefined;
  assert.equal((await request(`${WAKE_PATH}?handle=eva-000`, 'POST')).status, 403);
});

test('wake validates a single explicit handle before inventory and cannot allocate unknown handles', async () => {
  const { ctx, request } = fixture();
  let inventoryCalls = 0;
  registerParentView(ctx, {
    async status(...args) { inventoryCalls++; assert.deepEqual(args, []); return { nodes: [] }; },
    start() { assert.fail('invalid or unknown request must never allocate'); },
  });
  for (const query of ['', '?handle=', '?handle=../escape', '?handle=UPPER', `?handle=${'a'.repeat(33)}`, '?handle=eva-000&handle=eva-001']) {
    const response = await request(WAKE_PATH + query, 'POST');
    privateResponse(response);
    assert.equal(response.status, 400);
    assert.equal((await response.json()).code, 'invalid');
  }
  assert.equal(inventoryCalls, 0);
  const unknown = await request(`${WAKE_PATH}?handle=eva-unknown`, 'POST');
  assert.equal(unknown.status, 404);
  assert.equal((await unknown.json()).code, 'unknown');
  assert.equal(inventoryCalls, 1);
});

test('wake rejects stopping nodes and stop races, and sanitizes startup and inventory failures', async () => {
  for (const scenario of ['stopping', 'race', 'failed', 'inventory']) {
    const { ctx, request } = fixture();
    let starts = 0;
    registerParentView(ctx, {
      async status() {
        if (scenario === 'inventory') throw new Error(`${launch} ${secret}`);
        return { nodes: [{ handle: 'eva-000', status: scenario === 'stopping' || scenario === 'race' && starts ? 'stopping' : 'failed' }] };
      },
      async start() { starts++; throw new Error(`${launch} ${secret}`); },
    });
    const response = await request(`${WAKE_PATH}?handle=eva-000`, 'POST');
    privateResponse(response);
    const busy = scenario === 'stopping' || scenario === 'race';
    assert.equal(response.status, busy ? 409 : 503);
    const body = await response.json();
    assert.equal(body.code, busy ? 'busy' : 'start-failed');
    assert.ok(body.error.length > 10);
    assert.ok(!JSON.stringify(body).includes(secret));
    assert.ok(!JSON.stringify(body).includes('http:'));
    assert.equal(starts, scenario === 'stopping' || scenario === 'inventory' ? 0 : 1);
  }
});

test('duplicate wakes pass one fixed handle and never bind startup to request abort', async () => {
  const { ctx, request } = fixture();
  const started = Promise.withResolvers();
  const entered = Promise.withResolvers();
  const calls = [];
  registerParentView(ctx, {
    async status() { return { nodes: [{ handle: 'eva-007', status: 'starting' }] }; },
    async start(...args) { calls.push(args); if (calls.length === 2) entered.resolve(); return started.promise; },
  });
  const abort = new AbortController();
  const a = request(`${WAKE_PATH}?handle=eva-007`, 'POST', { signal: abort.signal });
  const b = request(`${WAKE_PATH}?handle=eva-007`, 'POST');
  await entered.promise;
  abort.abort();
  assert.deepEqual(calls, [[undefined, 'eva-007'], [undefined, 'eva-007']]);
  const safe = { handle: 'eva-007', status: 'running', workspace: '/same/home', mainSessionId: 'session-same' };
  started.resolve(safe);
  for (const response of await Promise.all([a, b])) {
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), safe);
  }
});

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
