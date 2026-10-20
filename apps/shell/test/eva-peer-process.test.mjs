import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { createServer } from 'node:net';
import { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import { Bus, MAGI_CONTACT_ID, messageDelivery } from '../../../packages/bus/dist/index.js';
import { principalFor } from '../../../packages/channel-peer/dist/worker.js';

async function freePort() {
  const server = createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port;
}
async function until(read, timeout = 15_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await read(); if (value) return value; await new Promise(resolve => setTimeout(resolve, 50)); }
  throw new Error('EVA process smoke timed out');
}
function launch(home, handle) {
  const child = spawn(process.execPath, [resolve('apps/eva/eva.mjs'), '--role', 'agent', handle], {
    cwd: resolve('.'), env: { ...process.env, MAGI_HOME: home, MAGI_NODE: process.execPath, MAGI_HOST: '1', MAGI_SERVER: '1', MAGI_CRASH_LIMIT: '1' }, stdio: ['pipe', 'pipe', 'pipe', 'ipc'],
  });
  let stderr = ''; child.stderr.on('data', chunk => { stderr += chunk; });
  let seq = 0; const pending = new Map(); const lines = createInterface({ input: child.stdout });
  lines.on('line', line => { let message; try { message = JSON.parse(line); } catch { return; } const item = pending.get(message.id); if (!item) return; if (message.type === 'res') { clearTimeout(item.timer); pending.delete(message.id); item.resolve(message.result); } else if (message.type === 'err') { clearTimeout(item.timer); pending.delete(message.id); item.reject(new Error(message.error)); } });
  return {
    child,
    ready: new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error(`launcher did not become ready: ${stderr}`)), 15_000); child.on('message', message => { if (message?.type === 'ready') { clearTimeout(timer); resolve(); } if (message?.type === 'failed') { clearTimeout(timer); reject(new Error(message.error)); } }); child.once('exit', code => { clearTimeout(timer); reject(new Error(`launcher exited ${code}: ${stderr}`)); }); }),
    invoke(method, params) { const id = String(++seq); return new Promise((resolve, reject) => { const timer = setTimeout(() => { pending.delete(id); reject(new Error(`owner RPC timed out: ${stderr}`)); }, 10_000); pending.set(id, { resolve, reject, timer }); child.stdin.write(`${JSON.stringify({ type: 'req', id, method, params })}\n`); }); },
    async stop() { if (child.exitCode === null && child.signalCode === null) {
      const closed = once(child, 'close');
      // Windows SIGTERM kills only the launcher without running its JS shutdown
      // handler. Reap its owned descendants before removing their SQLite files.
      if (process.platform === 'win32') {
        const killer = spawn('taskkill.exe', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore', windowsHide: true });
        const [code] = await once(killer, 'exit');
        assert.equal(code, 0, 'owned Windows launcher tree must stop');
      } else child.kill('SIGTERM');
      await closed;
    } lines.close(); for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new Error('stopped')); } },
  };
}

test('real independent EVA launchers exchange direct chat and expose owner API only over parent stdio, without ASP', { timeout: 30_000 }, async () => {
  const home = mkdtempSync(join(tmpdir(), 'magi-process-peers-'));
  const ports = [await freePort(), await freePort()];
  const buses = ['one', 'two'].map(name => new Bus(`@${name}.magi`, join(home, name)));
  const principals = buses.map(bus => principalFor(bus.identity.publicKey));
  buses.forEach((bus, index) => { bus.settings.set('peer.port', String(ports[index])); const other = 1 - index; bus.contacts.savePeer(bus.contacts.forPeer({ principal: principals[other], endpoint: `http://127.0.0.1:${ports[other]}` }).id); });
  const room = buses[0].chats.forPeer(principals[1]);
  // Correlated delivery avoids asking a real provider: this test has no credentials.
  const job = messageDelivery.send(buses[0], room.id, 'real launcher direct peer delivery', MAGI_CONTACT_ID, 'test', { peer: { replyTo: randomUUID() } });
  buses.forEach(bus => bus.close());
  const children = [launch(home, '@one.magi'), launch(home, '@two.magi')];
  let reader, jobs;
  try {
    await Promise.all(children.map(child => child.ready));
    reader = new DatabaseSync(join(home, 'two/memories/books.db'), { readOnly: true });
    await until(() => reader.prepare('SELECT id FROM books_messages WHERE content = ?').get('real launcher direct peer delivery'));
    jobs = new DatabaseSync(join(home, 'one/logs/jobs.db'), { readOnly: true });
    await until(() => jobs.prepare('SELECT status FROM jobs WHERE id = ?').get(job)?.status === 'completed');
    const profile = await children[1].invoke('owner.profile'); assert.equal(profile.handle, '@two.magi'); assert.equal(profile.owner, true);
    const receipt = await children[1].invoke('owner.chat', { content: 'local owner smoke' }); assert.ok(receipt.id > 0);
    const events = await children[1].invoke('owner.events'); assert.ok(events.some(event => event.content === 'local owner smoke' && event.role === 'user'));
    const response = await fetch(`http://127.0.0.1:${ports[1]}/owner.profile`); assert.equal(response.status, 404);
    assert.notEqual(ports[0], ports[1]);
  } finally { reader?.close(); jobs?.close(); await Promise.all(children.map(child => child.stop())); rmSync(home, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }); }
});
