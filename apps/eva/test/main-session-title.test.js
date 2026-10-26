import test from 'node:test';
import assert from 'node:assert/strict';
import { ensureMainSessionTitle } from '../main-session.js';

function fixture(current) {
  const calls = [];
  const ctx = {
    sessionQuery: { async readTitle(id) { calls.push(['read', id]); return current; } },
    sessionController: { async rename(request) { calls.push(['rename', request]); return { title: request.title, seq: 0 }; } },
    sessionPersistence: { async flush() { calls.push(['flush']); } },
  };
  return { ctx, calls };
}

for (const [name, current] of [
  ['blank', undefined],
  ['generated conversation title', { title: 'Simple addition question', source: { kind: 'provider' } }],
  ['older user title', { title: 'Old main', source: { kind: 'user' } }],
  ['equal fallback text', { title: 'EVA-001', source: { kind: 'fallback' } }],
  ['equal provider text', { title: 'EVA-001', source: { kind: 'provider' } }],
]) {
  test(`main node-name title pins ${name} through native rename then flush`, async () => {
    const { ctx, calls } = fixture(current);
    await ensureMainSessionTitle(ctx, 'session-main', 'eva-001');
    assert.deepEqual(calls, [
      ['read', 'session-main'],
      ['rename', { sessionId: 'session-main', title: 'EVA-001' }],
      ['flush'],
    ]);
  });
}

test('already-pinned node name adds no duplicate title event on restart', async () => {
  const { ctx, calls } = fixture({ title: 'EVA-001', source: { kind: 'user' } });
  await ensureMainSessionTitle(ctx, 'session-main', 'eva-001');
  assert.deepEqual(calls, [['read', 'session-main']]);
});

test('custom node names use the same uppercase label as the sidebar', async () => {
  const { ctx, calls } = fixture();
  await ensureMainSessionTitle(ctx, 'session-main', 'melchior');
  assert.equal(calls[1][1].title, 'MELCHIOR');
});

test('title read and rename failures propagate without a later write/barrier', async () => {
  for (const stage of ['read', 'rename']) {
    const { ctx, calls } = fixture();
    const failure = new Error(`${stage} failed`);
    if (stage === 'read') ctx.sessionQuery.readTitle = async () => { throw failure; };
    else ctx.sessionController.rename = async () => { throw failure; };
    await assert.rejects(ensureMainSessionTitle(ctx, 'session-main', 'eva-001'), (error) => error === failure);
    assert.ok(!calls.some(([kind]) => kind === 'flush' || kind === 'rename'));
  }
});

for (const accepted of [{ title: 'wrong', seq: 1 }, { title: 'EVA-001', seq: -1 }, { title: 'EVA-001' }]) {
  test(`unexpected native title acceptance fails closed: ${JSON.stringify(accepted)}`, async () => {
    const { ctx, calls } = fixture();
    ctx.sessionController.rename = async () => accepted;
    await assert.rejects(ensureMainSessionTitle(ctx, 'session-main', 'eva-001'), /not accepted/);
    assert.ok(!calls.some(([kind]) => kind === 'flush'));
  });
}

test('node-name initialization waits for persistence and propagates flush failure', async () => {
  const { ctx } = fixture();
  let release, started;
  const barrier = new Promise((resolve) => { release = resolve; });
  const flushing = new Promise((resolve) => { started = resolve; });
  ctx.sessionPersistence.flush = async () => { started(); await barrier; };
  let settled = false;
  const title = ensureMainSessionTitle(ctx, 'session-main', 'eva-001').then(() => { settled = true; });
  await flushing;
  assert.equal(settled, false);
  release();
  await title;
  assert.equal(settled, true);
  ctx.sessionPersistence.flush = async () => { throw new Error('flush failed'); };
  await assert.rejects(ensureMainSessionTitle(ctx, 'session-main', 'eva-001'), /flush failed/);
});
