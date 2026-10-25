import test from 'node:test';
import assert from 'node:assert/strict';
import { registerEmbeddedLogin, EMBED_PATH } from '../view.js';

function fixture(authorizeIndex) {
  let route, status, headers, body;
  const ctx = {
    connection: { authorizeIndex },
    effect(callback) { callback(); },
    webServer: { register(value) { route = value; return () => {}; } },
  };
  registerEmbeddedLogin(ctx);
  assert.equal(route.path, EMBED_PATH);
  return (url) => {
    route.handler({ method: 'GET', url, headers: { host: '127.0.0.1:1234' } }, {
      writeHead(value, values) { status = value; headers = values; },
      end(value) { body = value; },
    });
    return { status, headers, body };
  };
}

test('embed login delegates token validation and partitions only the issued native cookie', () => {
  const nativeCookie = 'dsh-auth-example=signed-value; Max-Age=3600; Path=/; HttpOnly; SameSite=Strict';
  const request = fixture((req, res) => {
    assert.equal(req.url, '/?token=test-token');
    assert.equal(req.headers.host, '127.0.0.1:1234');
    res.writeHead(303, { 'set-cookie': nativeCookie, location: './', 'cache-control': 'no-store' });
    res.end();
    return false;
  });
  const response = request(`${EMBED_PATH}?token=test-token`);
  assert.equal(response.status, 303);
  assert.equal(response.headers.location, '/#eva-main');
  assert.equal(response.headers['set-cookie'], nativeCookie.replace('SameSite=Strict', 'SameSite=None') + '; Secure; Partitioned');
  assert.equal(response.headers['cache-control'], 'no-store');
});

test('embed login does not authorize or mint a cookie when native authentication rejects', () => {
  const request = fixture((req, res) => {
    res.writeHead(401, { 'cache-control': 'no-store' });
    res.end('authentication required');
    return false;
  });
  const response = request(`${EMBED_PATH}?token=invalid`);
  assert.equal(response.status, 401);
  assert.equal(response.headers['set-cookie'], undefined);
  assert.equal(response.headers.location, undefined);
});
