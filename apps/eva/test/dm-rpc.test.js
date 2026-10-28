import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';
import { createDmRpc } from '../dm-rpc.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const envelope = (kind, fields = {}) => ({ type: 'eva-dm-rpc', version: 1, kind, id: randomUUID(), ...fields });

function channels() {
  const a = new EventEmitter();
  const b = new EventEmitter();
  for (const [source, target] of [[a, b], [b, a]]) {
    source.connected = true;
    source.sent = [];
    source.send = (message, callback) => {
      source.sent.push(message);
      queueMicrotask(() => {
        if (source.connected === false) return callback?.(new Error('disconnected'));
        target.emit('message', message);
        callback?.(null);
      });
      return true;
    };
  }
  return [a, b];
}

function pair(t, handler = (operation, payload) => ({ operation, payload }), options = {}) {
  const [a, b] = channels();
  const left = createDmRpc(a, handler, options);
  const right = createDmRpc(b, handler, options);
  t.after(() => { left.close(); right.close(); });
  return { a, b, left, right };
}

test('all operations round trip asynchronously in both directions with exact envelopes', async t => {
  const { a, b, left, right } = pair(t, async (operation, payload) => {
    await tick();
    return { operation, payload };
  });
  for (const operation of ['deliver', 'contacts', 'hello']) {
    assert.deepEqual(await left.call(operation, { text: 'hello' }), { operation, payload: { text: 'hello' } });
    assert.deepEqual(await right.call(operation, [1, null]), { operation, payload: [1, null] });
  }
  assert.deepEqual(Object.keys(a.sent[0]).sort(), ['id', 'kind', 'operation', 'payload', 'type', 'version']);
  assert.equal(a.sent[0].type, 'eva-dm-rpc');
  assert.equal(a.sent[0].version, 1);
  assert.match(a.sent[0].id, /^[0-9a-f-]{36}$/);
  assert.equal(b.sent[0].kind, 'response');
  assert.deepEqual(Object.keys(b.sent[0]).sort(), ['id', 'kind', 'type', 'value', 'version']);
});

test('connected is optional on a generic EventEmitter channel', async t => {
  const { a, b, left, right } = pair(t);
  delete a.connected;
  delete b.connected;
  assert.deepEqual(await left.call('hello', 1), { operation: 'hello', payload: 1 });
  assert.deepEqual(await right.call('contacts', 2), { operation: 'contacts', payload: 2 });
});

test('undefined becomes null; payload/result snapshots prevent shared-reference mutation', async t => {
  const { left } = pair(t, (_operation, payload) => payload);
  assert.equal(await left.call('hello'), null);
  const payload = { nested: ['original'] };
  const result = left.call('deliver', payload);
  payload.nested[0] = 'changed';
  assert.deepEqual(await result, { nested: ['original'] });
  const second = pair(t, () => undefined);
  assert.equal(await second.left.call('hello'), null);
});

test('sync throws, async rejections, and hostile/large error messages become bounded errors', async t => {
  const errors = [new Error('sync failure'), 'string failure', null, { get message() { throw new Error('getter'); } }, new Error('x'.repeat(100000))];
  const { left, b } = pair(t, () => { throw errors.shift(); });
  await assert.rejects(left.call('hello'), /sync failure/);
  await assert.rejects(left.call('hello'), /string failure/);
  await assert.rejects(left.call('hello'), /operation failed/);
  await assert.rejects(left.call('hello'), /operation failed/);
  await assert.rejects(left.call('hello'), error => error.message.length === 512);
  for (const message of b.sent) {
    assert.ok(message.error.length <= 512);
    assert.equal(Object.hasOwn(message, 'value'), false);
  }
  const other = pair(t, async () => { throw new Error('async failure'); });
  await assert.rejects(other.left.call('deliver'), /async failure/);
});

test('timeout removes pending state and ignores late and unknown responses without requests', async t => {
  const [a] = channels();
  const rpc = createDmRpc(a, () => assert.fail('responses are never requests'), { timeoutMs: 15, maxPending: 1 });
  t.after(() => rpc.close());
  // Keep the test alive: production request timers intentionally do not do so.
  const keepAlive = delay(35);
  await assert.rejects(rpc.call('hello'), /timed out/);
  const id = a.sent[0].id;
  a.emit('message', envelope('response', { id, value: 'late' }));
  a.emit('message', envelope('response', { value: 'unknown' }));
  const next = rpc.call('hello');
  a.emit('message', envelope('response', { id: a.sent[1].id, value: 'next' }));
  assert.equal(await next, 'next');
  assert.equal(a.sent.length, 2);
  await keepAlive;
});

test('timeouts are unrefed and cleared on response and close', async t => {
  const originalSet = globalThis.setTimeout;
  const originalClear = globalThis.clearTimeout;
  const timers = [];
  const cleared = [];
  globalThis.setTimeout = (...args) => {
    assert.equal(args[1], 10000);
    const timer = originalSet(...args);
    timers.push(timer);
    return timer;
  };
  globalThis.clearTimeout = timer => { cleared.push(timer); originalClear(timer); };
  t.after(() => { globalThis.setTimeout = originalSet; globalThis.clearTimeout = originalClear; });
  const { left, right } = pair(t);
  const first = left.call('hello');
  assert.equal(timers[0].hasRef(), false);
  await first;
  assert.ok(cleared.includes(timers[0]));
  right.close();
  const second = left.call('hello');
  const rejection = assert.rejects(second, /closed/);
  left.close();
  await rejection;
  assert.ok(cleared.includes(timers[1]));
});

test('explicit close, disconnect, and channel close reject pending and prevent new calls', async t => {
  for (const reason of ['explicit', 'disconnect', 'close']) {
    const [a] = channels();
    const rpc = createDmRpc(a, () => null);
    t.after(() => rpc.close());
    const result = assert.rejects(rpc.call('hello'), /closed|disconnect/);
    if (reason === 'explicit') rpc.close(); else a.emit(reason);
    await result;
    await assert.rejects(rpc.call('hello'), /closed|disconnect/);
    rpc.close();
    assert.equal(a.sent.length, 1);
  }
});

test('already disconnected endpoints reject and remove listeners, including silent disconnect', async t => {
  const [a] = channels();
  a.connected = false;
  const rpc = createDmRpc(a, () => null);
  await assert.rejects(rpc.call('hello'), /closed|disconnect/);
  assert.equal(a.listenerCount('message'), 0);
  const other = pair(t);
  other.a.connected = false;
  await assert.rejects(other.left.call('hello'), /disconnect/);
  assert.equal(other.a.listenerCount('message'), 0);
});

test('close removes only owned listeners and prevents queued handler execution', async () => {
  const [a] = channels();
  let executions = 0;
  const sentinel = () => {};
  for (const event of ['message', 'disconnect', 'close']) a.on(event, sentinel);
  const rpc = createDmRpc(a, () => { executions++; });
  a.emit('message', envelope('request', { operation: 'hello', payload: null }));
  rpc.close();
  await tick();
  a.emit('message', envelope('request', { operation: 'hello', payload: null }));
  for (const event of ['message', 'disconnect', 'close']) assert.deepEqual(a.listeners(event), [sentinel]);
  assert.equal(executions, 0);
  assert.equal(a.sent.length, 0);
});

test('close suppresses both resolved and rejected active handler responses', async t => {
  for (const fail of [false, true]) {
    let settle;
    const { left, right, b } = pair(t, () => new Promise((resolve, reject) => {
      settle = fail ? reject : resolve;
    }));
    const result = assert.rejects(left.call('deliver'), /closed/);
    await tick();
    right.close();
    settle(fail ? new Error('late failure') : 'late success');
    await tick();
    assert.equal(b.sent.length, 0);
    left.close();
    await result;
  }
});

test('malformed messages, accessors, oversized input, and unknown operations are ignored', async t => {
  let executions = 0;
  const { a } = pair(t, () => { executions++; });
  const valid = envelope('request', { operation: 'hello', payload: null });
  const cycle = {}; cycle.self = cycle;
  const getter = { get type() { assert.fail('must not invoke accessors'); } };
  const malformed = [null, undefined, 1, 'text', [], {}, cycle, getter,
    { ...valid, type: 'other' }, { ...valid, version: 2 }, { ...valid, kind: 'other' },
    { ...valid, id: 'not-uuid' }, { ...valid, operation: 'delete' }, { ...valid, extra: true },
    { ...valid, operation: undefined }, { ...valid, payload: () => null },
    { ...valid, payload: 'x'.repeat(48 * 1024) }, { ...valid, payload: 1n },
    envelope('response', { value: 1, error: 'both' }), envelope('response', { error: 42 }),
    envelope('response', { error: '' }), envelope('response', { error: 'x'.repeat(513) }),
    envelope('response'), envelope('response', { value: 1, operation: 'hello' })];
  const missingPayload = { ...valid }; delete missingPayload.payload; malformed.push(missingPayload);
  for (const message of malformed) assert.doesNotThrow(() => a.emit('message', message));
  await tick();
  assert.equal(executions, 0);
  assert.equal(a.sent.length, 0);
});

test('malformed response matching a pending ID cannot settle it or run the handler', async t => {
  const [a] = channels();
  const rpc = createDmRpc(a, () => assert.fail('must not execute'));
  t.after(() => rpc.close());
  const result = rpc.call('hello');
  const id = a.sent[0].id;
  a.emit('message', envelope('response', { id, value: 'bad', extra: 1 }));
  a.emit('message', envelope('response', { id, value: 'bad', error: 'bad' }));
  a.emit('message', envelope('response', { id, value: 'good' }));
  assert.equal(await result, 'good');
});

test('outbound validation rejects invalid operations, non-JSON, and oversize without sending', async t => {
  const { left, a } = pair(t);
  const cycle = {}; cycle.self = cycle;
  class HostileArray extends Array { toJSON() { assert.fail('must not invoke inherited toJSON'); } }
  await assert.rejects(left.call('unknown'), /operation/);
  for (const payload of [cycle, 1n, new Date(), NaN, { value: undefined }, Array(2), new HostileArray(), { toJSON() { throw new Error('not invoked'); } }]) {
    await assert.rejects(left.call('hello', payload), /JSON/);
  }
  await assert.rejects(left.call('hello', 'x'.repeat(48 * 1024)), /48KiB|large/);
  await assert.rejects(left.call('hello', '😀'.repeat(16000)), /48KiB|large/);
  await assert.rejects(left.call('hello', '\u0000'.repeat(10000)), /48KiB|large/);
  assert.equal(a.sent.length, 0);
});

test('serialized 48KiB boundary is inclusive and counts UTF-8 bytes', async t => {
  const { left, a } = pair(t, () => null);
  const baseBytes = Buffer.byteLength(JSON.stringify(envelope('request', { operation: 'hello', payload: '' })));
  await left.call('hello', 'x'.repeat(48 * 1024 - baseBytes));
  assert.equal(Buffer.byteLength(JSON.stringify(a.sent[0])), 48 * 1024);
  await assert.rejects(left.call('hello', 'x'.repeat(48 * 1024 - baseBytes + 1)), /48KiB/);
});

test('oversized and non-JSON handler results return a bounded matching error, not a timeout', async t => {
  for (const result of ['x'.repeat(48 * 1024), 1n, { value: undefined }]) {
    const { left, b } = pair(t, () => result);
    await assert.rejects(left.call('deliver'), /response.*JSON.*48KiB/);
    assert.ok(Buffer.byteLength(JSON.stringify(b.sent[0])) <= 48 * 1024);
  }
});

test('maxPending bounds outbound work and recovered capacity can be used', async t => {
  const [a] = channels();
  const rpc = createDmRpc(a, () => null, { maxPending: 2 });
  t.after(() => rpc.close());
  const first = rpc.call('hello');
  const second = rpc.call('hello');
  await assert.rejects(rpc.call('hello'), /outbound overload/);
  a.emit('message', envelope('response', { id: a.sent[0].id, value: 1 }));
  assert.equal(await first, 1);
  const third = rpc.call('hello');
  a.emit('message', envelope('response', { id: a.sent[1].id, value: 2 }));
  a.emit('message', envelope('response', { id: a.sent[2].id, value: 3 }));
  assert.deepEqual(await Promise.all([second, third]), [2, 3]);
});

test('inbound concurrency is 32 independently of outbound limits; duplicate active IDs ignored', async t => {
  const [a] = channels();
  const releases = [];
  let active = 0;
  let peak = 0;
  const rpc = createDmRpc(a, () => new Promise(resolve => {
    active++; peak = Math.max(peak, active);
    releases.push(() => { active--; resolve('ok'); });
  }), { maxPending: 1 });
  t.after(() => rpc.close());
  const first = envelope('request', { operation: 'hello', payload: null });
  a.emit('message', first); a.emit('message', first);
  for (let i = 0; i < 32; i++) a.emit('message', envelope('request', { operation: 'deliver', payload: null }));
  await tick();
  assert.equal(peak, 32);
  assert.equal(a.sent.length, 1);
  assert.match(a.sent[0].error, /inbound overload/);
  for (const release of releases.splice(0)) release();
  await tick();
  assert.equal(a.sent.length, 33);
  a.emit('message', envelope('request', { operation: 'contacts', payload: null }));
  await tick();
  assert.equal(active, 1);
  releases[0]();
  await tick();
  assert.equal(a.sent.length, 34);
});

test('send throws and callback errors release pending immediately without retry', async t => {
  for (const kind of ['throw', 'callback', 'async callback']) {
    const [a] = channels();
    let attempts = 0;
    a.send = (_message, callback) => {
      attempts++;
      if (kind === 'throw') throw new Error('send broke');
      if (kind === 'callback') callback(new Error('send broke'));
      else queueMicrotask(() => callback(new Error('send broke')));
    };
    const rpc = createDmRpc(a, () => null, { maxPending: 1 });
    t.after(() => rpc.close());
    await assert.rejects(rpc.call('hello'), /send failed.*send broke/);
    await assert.rejects(rpc.call('hello'), /send failed.*send broke/);
    assert.equal(attempts, 2);
  }
});

test('response send failures are caught and false send return is only backpressure', async t => {
  for (const kind of ['throw', 'callback']) {
    const [a] = channels();
    a.send = (_message, callback) => {
      if (kind === 'throw') throw new Error('response failed');
      queueMicrotask(() => callback(new Error('response failed')));
    };
    const rpc = createDmRpc(a, () => 'ok');
    t.after(() => rpc.close());
    a.emit('message', envelope('request', { operation: 'hello', payload: null }));
    await tick(); // An unhandled rejection would fail node:test.
  }
  const { a, left } = pair(t);
  const send = a.send;
  a.send = (...args) => { send(...args); return false; };
  assert.deepEqual(await left.call('hello', 1), { operation: 'hello', payload: 1 });
});

test('configuration requires a channel, handler, positive bounded timer and pending limit', () => {
  const [a] = channels();
  assert.throws(() => createDmRpc(null, () => null), /channel/);
  assert.throws(() => createDmRpc(a, null), /handler/);
  for (const options of [{ timeoutMs: 0 }, { timeoutMs: Infinity }, { timeoutMs: 2147483648 }, { timeoutMs: 0.5 }, { maxPending: 0 }, { maxPending: Infinity }, { maxPending: 1.5 }]) {
    assert.throws(() => createDmRpc(a, () => null, options), /limits/);
  }
  assert.equal(a.listenerCount('message'), 0);
});
