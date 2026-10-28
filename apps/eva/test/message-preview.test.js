import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createMessagePreviewReader, safePreview } from '../message-preview.js';
import { createMessagePreviewReader as createShellReader } from '../../magi-shell/message-preview.js';

const user = (seq, text) => ({ seq, type: 'user/message', surfaceOp: 'append', data: { source: { kind: 'user' }, content: [{ type: 'text', text }] } });
const assistant = (seq, content) => ({ seq, type: 'assistant/message', surfaceOp: 'append', data: { message: { content } } });
function fixture(createReader = createMessagePreviewReader) {
  let events = [], cursor = -1, fail = false, disposed = 0, materialized = 0;
  const reads = [];
  const read = createReader({ sessionQuery: { async observeSession(id, options) {
    reads.push({ id, options });
    if (fail) throw new Error('private-token-must-not-leak');
    return { cursor, get events() { materialized++; return events; }, [Symbol.dispose]() { disposed++; } };
  } } });
  return { read, reads, set(value, through = value.at(-1)?.seq ?? -1) { events = value; cursor = through; },
    fail(value) { fail = value; }, get disposed() { return disposed; }, get materialized() { return materialized; } };
}

test('reader returns latest actual text, ignores tools, reasoning, injected messages, and replacement summaries', async () => {
  const f = fixture();
  assert.deepEqual(await f.read('main'), { state: 'empty' });
  f.set([user(0, 'human question'), assistant(1, [{ type: 'reasoning', text: 'hidden' }, { type: 'text', text: ' answer\n here ' }]),
    { seq: 2, type: 'tool/result', data: { message: { content: [{ type: 'text', text: 'tool secret' }] } } },
    { ...user(3, 'system reminder'), data: { source: { kind: 'system' }, content: [{ type: 'text', text: 'injected' }] } },
    { ...user(4, 'compacted summary'), surfaceOp: 'replace' }]);
  assert.deepEqual(await f.read('main'), { state: 'message', text: 'answer here' });
  assert.equal(f.disposed, 2);
  assert.ok(f.reads.every(({ id, options }) => id === 'main' && options.projectionMode === 'none'));
});

test('unchanged polls avoid materializing logs; changed polls only examine the new seq suffix', async () => {
  const f = fixture();
  const old = user(0, 'first');
  f.set([old]);
  await f.read('main');
  Object.defineProperty(old, 'data', { get() { throw new Error('old message rescanned'); } });
  assert.deepEqual(await f.read('main'), { state: 'message', text: 'first' });
  assert.equal(f.materialized, 1);
  f.set([old, user(10, 'new suffix')], 10);
  assert.deepEqual(await f.read('main'), { state: 'message', text: 'new suffix' });
  assert.equal(f.disposed, 3);
});

test('failures are unavailable, retry recovers, cursor rewind and session change clear stale text', async () => {
  const f = fixture();
  f.set([user(4, 'old')]);
  assert.deepEqual(await f.read('main'), { state: 'message', text: 'old' });
  f.fail(true);
  assert.deepEqual(await f.read('main'), { state: 'unavailable' });
  f.fail(false); f.set([]);
  assert.deepEqual(await f.read('main'), { state: 'empty' });
  f.set([user(0, 'child')]);
  assert.deepEqual(await f.read('child-main'), { state: 'message', text: 'child' });
  assert.deepEqual(await f.read(undefined), { state: 'unavailable' });
});

test('attachment-only latest messages never retain stale text; preview payload is bounded and whitelisted', async () => {
  const f = fixture();
  f.set([user(0, 'old'), { ...user(1, ''), data: { source: { kind: 'user' }, content: [{ type: 'image', url: 'private://attachment' }] } }]);
  assert.deepEqual(await f.read('main'), { state: 'attachment' });
  assert.deepEqual(safePreview({ state: 'message', text: '😀'.repeat(300), token: 'secret' }), { state: 'message', text: '😀'.repeat(240) });
  assert.deepEqual(safePreview({ state: 'unknown', token: 'secret' }), { state: 'unavailable' });
});

const notice = (text, kind = 'eva-user-dm') => ({ id: 'dm-notice', source: { kind, peer: 'eva-001', messageId: 'notice', text },
  content: [{ type: 'text', text: 'MODEL-ONLY BACKGROUND FRAMING SECRET ' + JSON.stringify(text) }] });
const spliced = (seq, inserted, target = 'next-step') => ({ seq, type: 'agent/inbox/spliced', data: { target, inserted, start: 0, removedCount: 0 } });
const consumeNotice = (seq, message) => ({ seq, type: 'user/message', surfaceOp: 'append', data: message });

for (const [bundle, createReader] of [['EVA', createMessagePreviewReader], ['shell', createShellReader]]) {
  test(`${bundle}: notification-only native log previews raw source body immediately, never model framing`, async () => {
    const f = fixture(createReader);
    const raw = '<script>alert("x")</script> & **literal** @user "你好"';
    const msg = notice(raw);
    f.set([spliced(1, [msg])]);
    assert.deepEqual(await f.read('main'), { state: 'message', text: raw });
    f.set([spliced(1, [msg]), consumeNotice(2, msg)]);
    assert.deepEqual(await f.read('main'), { state: 'message', text: raw });
    assert.ok(f.reads.every(({ id, options }) => id === 'main' && options.projectionMode === 'none'));
  });

  test(`${bundle}: consuming an old background context cannot replace newer human or model previews`, async () => {
    for (const latest of [user(2, 'new human'), assistant(2, [{ type: 'text', text: 'new model' }])]) {
      const f = fixture(createReader), msg = notice('old notice');
      const events = [spliced(1, [msg]), latest];
      f.set(events);
      const before = await f.read('main');
      f.set([...events, consumeNotice(3, msg)]);
      assert.deepEqual(await f.read('main'), before);
      // Full-history replay must also not move the old notice to consumption.
      const fresh = fixture(createReader);
      fresh.set([...events, consumeNotice(3, msg)]);
      assert.deepEqual(await fresh.read('main'), before);
    }
    const f = fixture(createReader);
    f.set([consumeNotice(3, notice('splice outside loaded history'))]);
    assert.deepEqual(await f.read('main'), { state: 'empty' });
  });

  test(`${bundle}: malformed arrays/sources/text and unrelated native events do not leak or clear previews`, async () => {
    const f = fixture(createReader);
    f.set([user(0, 'keep'), spliced(1, null), spliced(2, {}), spliced(3, [null, {}, { source: null }, { source: 'eva-user-dm' },
      notice(undefined), notice(null), notice(7), notice({ secret: 'no' }), notice(['no']), notice('  \n\t '),
      notice('peer hidden', 'eva-peer-dm'), notice('human inbox hidden', 'user'), notice('outgoing hidden', 'eva-user-dm-outgoing')]),
      spliced(4, [notice('next-turn hidden')], 'next-turn'),
      { seq: 5, type: 'agent/inbox/spliced', data: null },
      { seq: 6, type: 'system/message', data: { message: notice('system hidden') } },
      { seq: 7, type: 'tool/result', data: { message: notice('tool hidden') } },
      assistant(8, [{ type: 'reasoning', text: 'reasoning hidden' }]),
      consumeNotice(9, notice('consumed peer hidden', 'eva-peer-dm'))]);
    assert.deepEqual(await f.read('main'), { state: 'message', text: 'keep' });
  });

  test(`${bundle}: notification normalization and Unicode cap retain existing bounded preview policy`, async () => {
    const f = fixture(createReader);
    f.set([spliced(1, [notice(' \n 原样\t body  \r\n 😀 ')])]);
    assert.deepEqual(await f.read('main'), { state: 'message', text: '原样 body 😀' });
    f.set([spliced(2, [notice('  😀'.repeat(300))])]);
    const result = await f.read('main');
    assert.equal(Array.from(result.text).length, 240);
    assert.equal(result.text, Array.from('😀 '.repeat(300).trim()).slice(0, 240).join(''));
    assert.deepEqual(Object.keys(result).sort(), ['state', 'text']);
  });
}

test('standalone shell and EVA packages carry identical preview semantics', async () => {
  assert.equal(await readFile(new URL('../message-preview.js', import.meta.url), 'utf8'),
    await readFile(new URL('../../magi-shell/message-preview.js', import.meta.url), 'utf8'));
});
