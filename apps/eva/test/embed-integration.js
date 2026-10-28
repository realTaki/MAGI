// Focused real-Host HTTP authentication check, not a browser/visual test.
// Run with MAGI's Electron in Node mode; no HOME change or user profile access.
import assert from 'node:assert/strict';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { join, dirname, basename } from 'node:path';
import { EvaRuntime, desktopRuntime } from '../runtime.js';
import { EMBED_PATH } from '../view.js';

const workspace = await realpath(process.cwd());
const root = await realpath(await mkdtemp(join(workspace, '.eva-embed-check-')));
const manager = new EvaRuntime(desktopRuntime(process.argv[2]), { homeRoot: root, startupTimeout: 45000 });
const get = (url, headers) => fetch(url, { headers, redirect: 'manual', signal: AbortSignal.timeout(5000) });
try {
  const node = await manager.start(root, 'eva-embed-check', new AbortController().signal);
  const standalone = await get(manager.launchUrl(node.handle));
  assert.equal(standalone.status, 303);
  assert.ok(standalone.headers.getSetCookie().every((value) => value.includes('SameSite=Strict') && !value.includes('Partitioned')));
  await standalone.arrayBuffer();

  const loginURL = new URL(manager.launchUrl(node.handle));
  loginURL.pathname = EMBED_PATH;
  const login = await get(loginURL);
  assert.equal(login.status, 303);
  assert.equal(login.headers.get('location'), '/#eva-main');
  const cookies = login.headers.getSetCookie();
  assert.ok(cookies.length > 0);
  assert.ok(cookies.every((value) => ['HttpOnly', 'SameSite=None', 'Secure', 'Partitioned'].every((attribute) => value.includes(attribute))));
  await login.arrayBuffer();
  // Deliberately never print credential-bearing URLs, headers or response bodies.
  const headers = { Cookie: cookies.map((value) => value.split(';', 1)[0]).join('; ') };
  const shell = await get(node.origin, headers);
  assert.equal(shell.status, 200);
  const shellText = await shell.text();
  assert.ok(shellText.includes('"id":"@local/eva"'), 'Real child boot graph must activate the EVA Client, not merely serve its Host API');
  const view = await get(new URL('/api/eva/view', node.origin), headers);
  assert.equal(view.status, 200);
  assert.ok((await view.json()).mainSessionId === node.mainSessionId);

  loginURL.search = '?token=invalid';
  const rejected = await get(loginURL);
  assert.equal(rejected.status, 401);
  assert.equal(rejected.headers.getSetCookie().length, 0);
  await rejected.arrayBuffer();
  console.log('PASS: native standalone Strict cookie unchanged; embed signed partitioned cookie authenticates shell/API; invalid token rejected.');
} finally {
  await manager.dispose();
  // Verify the resolved deletion target is the unique fixture we just created.
  if (dirname(root) !== workspace || !basename(root).startsWith('.eva-embed-check-') || await realpath(root) !== root) throw new Error('Unsafe fixture cleanup path');
  await rm(root, { recursive: true, force: true });
}
