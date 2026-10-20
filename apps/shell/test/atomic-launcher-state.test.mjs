import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { writeJsonAtomic } from '../../eva/atomic-json.mjs';

for (const name of ['launcher.json', 'replace-failed.json']) {
  test(`${name} publishes only complete snapshots and retains the prior state on failure`, () => {
    const home = fs.mkdtempSync(join(tmpdir(), 'magi-atomic-state-'));
    const file = join(home, name);
    const previous = { current: 'old', previous: 'older', job: 3 };
    const next = { current: 'new', previous: 'old', job: 4 };
    const read = () => JSON.parse(fs.readFileSync(file, 'utf8'));
    try {
      writeJsonAtomic(file, previous);
      for (const failure of ['partial-write', 'rename']) {
        assert.throws(() => writeJsonAtomic(file, next, {
          ...fs,
          writeFileSync(fd, data) {
            fs.writeFileSync(fd, failure === 'partial-write' ? '{' : data);
            assert.deepEqual(read(), previous);
            if (failure === 'partial-write') throw new Error('injected write failure');
          },
          renameSync() { throw new Error('injected rename failure'); },
        }), /injected/);
        assert.deepEqual(read(), previous);
        assert.deepEqual(fs.readdirSync(home), [name]);
      }
      writeJsonAtomic(file, next, {
        ...fs,
        renameSync(source, target) {
          assert.deepEqual(read(), previous);
          fs.renameSync(source, target);
          assert.deepEqual(read(), next);
        },
      });
      assert.deepEqual(read(), next);
      assert.deepEqual(fs.readdirSync(home), [name]);
    } finally { fs.rmSync(home, { recursive: true, force: true }); }
  });
}

test('a concurrent reader sees only complete launcher snapshots during replacement', async () => {
  const { Worker } = await import('node:worker_threads');
  const home = fs.mkdtempSync(join(tmpdir(), 'magi-state-reader-'));
  const file = join(home, 'launcher.json');
  const stop = new SharedArrayBuffer(4);
  writeJsonAtomic(file, { revision: 0, payload: 'x'.repeat(4096) });
  const reader = new Worker(`
    const { parentPort, workerData } = require('node:worker_threads');
    const { readFileSync } = require('node:fs');
    const stop = new Int32Array(workerData.stop);
    let reads = 0;
    parentPort.postMessage('ready');
    do {
      const state = JSON.parse(readFileSync(workerData.file, 'utf8'));
      if (!Number.isInteger(state.revision) || state.payload !== 'x'.repeat(4096)) throw new Error('partial snapshot');
      reads++;
    } while (!Atomics.load(stop, 0));
    parentPort.postMessage(reads);
  `, { eval: true, workerData: { file, stop } });
  const ready = new Promise((resolve, reject) => { reader.once('message', resolve); reader.once('error', reject); });
  const done = new Promise((resolve, reject) => {
    reader.on('message', value => { if (typeof value === 'number') resolve(value); });
    reader.once('error', reject);
  });
  try {
    await ready;
    for (let revision = 1; revision <= 150; revision++) writeJsonAtomic(file, { revision, payload: 'x'.repeat(4096) });
    Atomics.store(new Int32Array(stop), 0, 1);
    assert.ok(await done > 0);
    assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).revision, 150);
  } finally {
    Atomics.store(new Int32Array(stop), 0, 1);
    await reader.terminate();
    fs.rmSync(home, { recursive: true, force: true });
  }
});

test('replacement retries transient sharing locks but leaves old state after a bounded failure', () => {
  const home = fs.mkdtempSync(join(tmpdir(), 'magi-state-lock-'));
  const file = join(home, 'launcher.json');
  const read = () => JSON.parse(fs.readFileSync(file, 'utf8'));
  try {
    writeJsonAtomic(file, { revision: 'old' });
    let attempts = 0;
    writeJsonAtomic(file, { revision: 'new' }, { ...fs, renameSync(source, target) {
      assert.deepEqual(read(), { revision: 'old' });
      if (++attempts < 4) throw Object.assign(new Error('sharing lock'), { code: ['EPERM', 'EACCES', 'EBUSY'][attempts - 1] });
      fs.renameSync(source, target);
    } });
    assert.equal(attempts, 4);
    assert.deepEqual(read(), { revision: 'new' });
    attempts = 0;
    assert.throws(() => writeJsonAtomic(file, { revision: 'never' }, { ...fs, renameSync() {
      attempts++;
      throw Object.assign(new Error('persistent lock'), { code: 'EPERM' });
    } }), /persistent lock/);
    assert.equal(attempts, 50);
    assert.deepEqual(read(), { revision: 'new' });
    assert.deepEqual(fs.readdirSync(home), ['launcher.json']);
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});
