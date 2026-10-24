import test from 'node:test';
import assert from 'node:assert/strict';
import { readProcessTable, createProcessOwner } from '../process-owner.mjs';

test('Darwin queries BSD numeric process groups without sid or the sess kernel pointer', async () => {
  const table = await readProcessTable({ platform: 'darwin', run: async (file, args) => {
    assert.equal(file, '/bin/ps');
    assert.deepEqual(args, ['-axo', 'pid=,ppid=,pgid=']);
    return { stdout: ' 42 1 42\n 43 42 42\n 80 1 80\n' };
  } });
  assert.deepEqual(table, [{ pid: 42, parent: 1, group: 42, session: null, birth: null }, { pid: 43, parent: 42, group: 42, session: null, birth: null }, { pid: 80, parent: 1, group: 80, session: null, birth: null }]);
});

test('Linux retains numeric session IDs for descendants in another process group', async () => {
  const table = await readProcessTable({ platform: 'linux', run: async (_file, args) => {
    assert.deepEqual(args, ['-axo', 'pid=,ppid=,pgid=,sid=']);
    return { stdout: '42 1 42 42\n43 1 43 42\n' };
  } });
  assert.equal(table[1].session, 42);
});

test('a successful real owned command remains successful after native process reaping', async (t) => {
  const owner = createProcessOwner();
  t.after(() => owner.close());
  assert.equal((await owner.command(process.execPath, ['-e', 'console.log("native-command-ok")'], { env: process.env })).trim(), 'native-command-ok');
});

async function darwinGroupFailure(t, denyIndividual = false) {
  const platform = Object.getOwnPropertyDescriptor(process, 'platform');
  const kill = process.kill.bind(process);
  const owner = createProcessOwner({ graceMs: 100 });
  let child;
  const signals = [];
  const mocked = t.mock.method(process, 'kill', (pid, signal) => {
    signals.push({ pid, signal });
    if (pid === -child?.pid || (denyIndividual && pid === child?.pid)) {
      throw Object.assign(new Error('kill EPERM'), { code: 'EPERM' });
    }
    return kill(pid, signal);
  });
  Object.defineProperty(process, 'platform', { ...platform, value: 'darwin' });
  t.after(async () => {
    mocked.mock.restore();
    Object.defineProperty(process, 'platform', platform);
    await owner.close();
  });
  child = owner.spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
  const exited = new Promise((resolve) => child.once('exit', resolve));
  await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
  return { owner, child, exited, signals };
}

test('Darwin group EPERM falls back to owned PIDs and still reaps the real child', { timeout: 10_000, skip: process.platform === 'win32' }, async (t) => {
  const { owner, child, exited, signals } = await darwinGroupFailure(t);
  await owner.signal(child);
  await exited;
  await owner.reap(child);
  assert.ok(signals.some(({ pid, signal }) => pid === -child.pid && signal === 'SIGTERM'));
  assert.ok(signals.some(({ pid, signal }) => pid === child.pid && signal === 'SIGTERM'));
});

test('Darwin fallback preserves a real per-PID permission failure', { timeout: 10_000, skip: process.platform === 'win32' }, async (t) => {
  const { owner, child, signals } = await darwinGroupFailure(t, true);
  await assert.rejects(owner.signal(child), { code: 'EPERM' });
  assert.ok(signals.some(({ pid }) => pid === child.pid), 'must check the individual owned process');
});
