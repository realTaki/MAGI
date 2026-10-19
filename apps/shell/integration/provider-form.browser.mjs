// Isolated Chromium smoke test. No ASP server, provider endpoint, or real credential is used.
import { build } from 'esbuild';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
const dir = await mkdtemp(join(tmpdir(), 'magi-card-browser-'));
let browser;
let server;
try {
  const result = await build({ entryPoints: [resolve('apps/shell/integration/provider-form.browser.tsx')], bundle: true, write: false, format: 'iife', jsx: 'automatic', plugins: [{ name: 'mock-asp', setup(build) {
    build.onResolve({ filter: /^\.\/asp$/ }, () => ({ path: 'asp', namespace: 'mock' }));
    build.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({ contents: `
      window.__cardTest = {calls: [], resolve: null};
      export async function listProfiles() { return [{handle:'@test.magi',public_key:'synthetic-public-key'}]; }
      export async function encryptSecret(key,value) { if(value !== 'synthetic-browser-key') throw Error('unexpected synthetic key'); return 'synthetic-ciphertext'; }
      export async function sendAspMessage(chat,text,metadata) { window.__cardTest.calls.push(metadata); await new Promise(r => window.__cardTest.resolve=r); }
    `, loader: 'js' }));
  } }] });
  const html = join(dir, 'index.html');
  const styles = (await readFile('apps/shell/web/src/styles.css', 'utf8')).replace('@import "tailwindcss";', '');
  await writeFile(html, '<!doctype html><meta charset="utf-8"><style>' + styles + '</style><div id="root"></div><script>' + result.outputFiles[0].text.replaceAll('</script', '<\\/script') + '</script>');
  const document = await readFile(html);
  server = createServer((_request, response) => { response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end(document); });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const pageUrl = `http://127.0.0.1:${server.address().port}`;
  browser = spawn(process.env.CHROMIUM ?? '/usr/bin/chromium', ['--headless', '--no-sandbox', '--disable-dev-shm-usage', '--no-first-run', '--disable-gpu', '--disable-background-networking', '--remote-debugging-port=0', '--user-data-dir=' + join(dir, 'profile'), pageUrl], { detached: true, stdio: 'ignore', env: { ...process.env, XDG_CONFIG_HOME: join(dir, 'config'), XDG_CACHE_HOME: join(dir, 'cache') } });
  let port;
  for (let i = 0; i < 100; i++) {
    try { port = (await readFile(join(dir, 'profile/DevToolsActivePort'), 'utf8')).split('\n')[0]; break; } catch { await sleep(100); }
  }
  assert.ok(port, 'Chromium debugging endpoint');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find((item) => item.type === 'page');
  assert.ok(target, 'Chromium page');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let next = 0;
  const pending = new Map();
  socket.onmessage = ({data}) => { const reply = JSON.parse(data); if(pending.has(reply.id)) { pending.get(reply.id)(reply); pending.delete(reply.id); } };
  const evaluate = (expression) => new Promise((resolve) => { const id = ++next; pending.set(id, resolve); socket.send(JSON.stringify({id, method:'Runtime.evaluate', params:{expression, returnByValue:true}})); });
  let outcome;
  for (let i = 0; i < 100; i++) {
    const reply = await evaluate('JSON.stringify({state:document.body?.dataset.result,text:document.body?.innerText})');
    outcome = JSON.parse(reply.result.result.value || '{}');
    if (outcome.state) break;
    await sleep(100);
  }
  socket.close();
  assert.equal(outcome?.state, 'PASS', outcome?.text?.slice(-1000));
  console.log('PASS Chromium provider form: zh/en/ja, defaults, custom, clearing secrets, duplicate submit, encrypted transport, pending/saved/failed/cancelled, direct retry, profile deep links, avatar start/stop');
} finally { server?.close(); if(browser?.pid) { try { process.kill(-browser.pid, 'SIGKILL'); } catch {} await sleep(100); } await rm(dir, { recursive: true, force: true }); }
