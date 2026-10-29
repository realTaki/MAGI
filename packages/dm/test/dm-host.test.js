import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { DmHost, inboxRecordId, nativeAdmission, validateEnvelope } from '../dm-host.js';
import { DmStore, dmIdFor } from '../dm-store.js';

const clone = value => JSON.parse(JSON.stringify(value));

// This fake implements only public native APIs. Its raw log survives a simulated
// restart independently from Agent instances and includes canceled/claimed splices.
function native(workspace) {
  let sessions = new Map();
  let durable = [];
  const calls = { create: [], resolve: [], followup: [], inject: [], remove: [], flush: 0 };
  const control = { failFlush: null };
  const save = () => { durable = clone([...sessions]); };
  function get(id) {
    const state = sessions.get(id);
    assert.ok(state, `native Session ${id} must exist`);
    return state;
  }
  function splice(state, start, removedCount, inserted, outcome, target = 'next-turn') {
    const list = target === 'next-turn' ? state.inbox : state.nextStep;
    list.splice(start, removedCount, ...clone(inserted));
    state.events.push({ type: 'agent/inbox/spliced', data: {
      target, start, removedCount, inserted: clone(inserted), ...(outcome ? { outcome } : {}),
    } });
  }
  function remove(id, messageId, outcome = 'canceled') {
    const state = get(id);
    calls.remove.push({ sessionId: id, id: messageId, outcome });
    const index = state.inbox.findIndex(message => message.id === messageId);
    const message = index < 0 ? null : state.inbox[index];
    if (index >= 0) splice(state, index, 1, [], outcome);
    return message;
  }
  const ctx = {
    sessionController: {
      async create(options) {
        assert.deepEqual(Object.keys(options).sort(), ['cwd', 'sessionId']);
        assert.equal(options.cwd, workspace);
        assert.match(options.sessionId, /^session-[0-9a-f-]{36}$/);
        assert.equal(sessions.has(options.sessionId), false, 'never recreate an existing native Session');
        calls.create.push(clone(options));
        sessions.set(options.sessionId, {
          session: { id: options.sessionId, cwd: options.cwd, isSeeded: false },
          events: [], inbox: [], nextStep: [], status: 'idle',
        });
        return { sessionId: options.sessionId };
      },
      async inspect(id) {
        const state = get(id);
        return clone({ meta: state.session, events: state.events });
      },
      async resolveAgent(id) {
        calls.resolve.push(id);
        const state = get(id);
        return { agent: {
          id,
          get status() { return state.status; },
          followup(message) {
            assert.deepEqual(Object.keys(message).sort(), ['content', 'id', 'role', 'source']);
            assert.equal(message.id, `dm-${message.source.messageId}`);
            assert.equal(message.role, 'user');
            assert.equal(message.source.kind, 'eva-dm');
            assert.equal(message.source.dmId.length, 64);
            for (const value of [message, message.source, message.content, ...message.content]) assert.equal(Object.isFrozen(value), true);
            assert.equal(message.content.length, 1);
            assert.equal(message.content[0].type, 'text');
            assert.match(message.content[0].text, /^\[MAGI-DM\] from @[a-z][a-z0-9-]*:\n\n/);
            assert.equal(state.inbox.some(item => item.id === message.id), false, 'no duplicate native pending IDs');
            calls.followup.push({ sessionId: id, message: clone(message) });
            splice(state, state.inbox.length, 0, [message]);
            state.status = 'running';
          },
          inject(message) {
            assert.deepEqual(Object.keys(message).sort(), ['content', 'id', 'role', 'source']);
            assert.equal(message.id, `dm-out-${message.source.messageId}`);
            assert.equal(message.role, 'user');
            assert.equal(message.source.kind, 'eva-dm-outgoing');
            assert.equal(message.source.dmId.length, 64);
            for (const value of [message, message.source, message.content, ...message.content]) assert.equal(Object.isFrozen(value), true);
            assert.equal(message.content.length, 1);
            assert.equal(message.content[0].type, 'text');
            assert.match(message.content[0].text, /^Explicit outgoing DM to @[a-z][a-z0-9-]*/);
            assert.equal(state.nextStep.some(item => item.id === message.id), false, 'outgoing context must not duplicate pending IDs');
            const status = state.status;
            calls.inject.push({ sessionId: id, message: clone(message) });
            splice(state, state.nextStep.length, 0, [message], undefined, 'next-step');
            assert.equal(state.status, status, 'native inject(next-step, false) never wakes the Agent');
          },
          inbox: { remove: messageId => remove(id, messageId) },
        } };
      },
    },
    sessionQuery: {
      async readSession(id) {
        const state = get(id);
        return clone({ session: state.session, events: state.events });
      },
    },
    sessionPersistence: {
      async flush() {
        calls.flush++;
        const failure = control.failFlush;
        control.failFlush = null;
        if (failure === 'before') throw new Error('native flush before disk');
        save();
        if (failure === 'after') throw new Error('native flush ambiguous after disk');
      },
    },
  };
  return {
    ctx, calls, control, state: get,
    durableState(id) { return clone(new Map(durable).get(id)); },
    async flush() { await ctx.sessionPersistence.flush(); },
    commitInjected(id, messageId) {
      const state = get(id);
      const index = state.nextStep.findIndex(message => message.id === messageId);
      assert.ok(index >= 0);
      const message = state.nextStep[index];
      splice(state, index, 1, [], 'claimed', 'next-step');
      state.events.push({ type: 'user/message', data: message });
    },
    restart() {
      sessions = new Map(clone(durable));
      for (const state of sessions.values()) state.status = 'idle';
    },
    claim(id, messageId) {
      const message = remove(id, messageId, 'claimed');
      get(id).status = 'running';
      return message;
    },
    commit(id, messageId) {
      const state = get(id);
      const message = this.claim(id, messageId);
      assert.ok(message);
      state.events.push({ type: 'user/message', data: message });
      state.status = 'idle';
    },
    cancel(id, messageId) { remove(id, messageId); get(id).status = 'idle'; },
  };
}

async function fixture(t) {
  const base = await realpath(tmpdir());
  const root = await mkdtemp(join(base, 'magi-dm-host-test-'));
  const entries = new Map();
  const hosts = new Set();
  const wire = [];
  const contacts = async () => [...entries.values()].map(entry => entry.store.identity());
  function host(entry) {
    const instance = new DmHost(entry.native.ctx, entry.store, {
      workspace: entry.workspace, contacts,
      transmit: async envelope => {
        await entry.beforeTransmit?.(envelope);
        wire.push(clone(envelope));
        const target = entries.get(envelope.to);
        if (!target?.online) throw new Error('offline secret https://user:password@example.test');
        return target.host.receive(envelope, entry.store.identity().handle);
      },
    });
    hosts.add(instance);
    return instance;
  }
  t.after(async () => {
    await Promise.all([...hosts].map(instance => instance.close()));
    assert.equal(resolve(root), root);
    assert.ok(root.startsWith(join(base, 'magi-dm-host-test-')));
    await rm(root, { recursive: true, force: true });
  });
  async function open(handle) {
    const profile = await mkdtemp(join(root, `${handle}-`));
    const workspace = profile;
    const store = await DmStore.open(profile, handle);
    const entry = { profile, workspace, store, native: native(workspace), online: true };
    entry.host = host(entry);
    entries.set(handle, entry);
    return entry;
  }
  const alice = await open('alice');
  const bob = await open('bob');
  async function restart(entry, initialHandle = entry.store.identity().handle) {
    await entry.host.close();
    entry.native.restart();
    entry.store = await DmStore.open(entry.profile, initialHandle);
    entry.host = host(entry);
    return entry;
  }
  return { alice, bob, wire, contacts, open, restart };
}

const message = (overrides = {}) => ({ version: 1, id: 'message-1', from: 'alice', to: 'bob',
  dmId: dmIdFor('alice', 'bob'), text: 'explicit hello', createdAt: 100, ...overrides });

test('explicit send wakes only recipient; sender context is injected quietly in a distinct local Session', async t => {
  const { alice, bob, wire } = await fixture(t);
  const result = await alice.host.send({ to: 'bob', text: 'hello Bob', messageId: 'hello-1' });
  assert.equal(result.status, 'accepted');
  assert.equal(wire.length, 1);
  assert.equal(alice.native.calls.followup.length, 0);
  assert.equal(bob.native.calls.followup.length, 1);
  const a = await alice.host.pair('bob');
  const b = await bob.host.pair('alice');
  assert.equal(a.dmId, b.dmId);
  assert.notEqual(a.sessionId, b.sessionId);
  assert.equal(alice.native.calls.inject.length, 1);
  assert.equal(bob.native.calls.inject.length, 0);
  assert.equal(alice.native.calls.inject[0].sessionId, a.sessionId);
  assert.equal(alice.native.calls.inject[0].message.id, 'dm-out-hello-1');
  assert.deepEqual(alice.native.calls.inject[0].message.source, {
    kind: 'eva-dm-outgoing', peer: 'bob', messageId: 'hello-1', dmId: a.dmId,
  });
  assert.match(alice.native.calls.inject[0].message.content[0].text, /\n\nhello Bob$/);
  assert.equal(wire[0].text, 'hello Bob', 'wire contains explicit peer text, not local provenance or output');
  assert.equal(alice.native.state(a.sessionId).status, 'idle');
  assert.equal(alice.native.state(a.sessionId).inbox.length, 0);
  assert.equal(alice.native.state(a.sessionId).nextStep.length, 1);
  assert.equal(bob.native.calls.followup[0].sessionId, b.sessionId);
  assert.equal(bob.native.calls.followup[0].message.content[0].text, '[MAGI-DM] from @alice:\n\nhello Bob');
  assert.equal(bob.native.calls.followup[0].message.source.messageId, 'hello-1');
  assert.equal((await alice.host.pair('bob')).sessionId, a.sessionId);
  assert.equal((await bob.host.pair('alice')).sessionId, b.sessionId);
  assert.equal(alice.native.calls.create.length, 1);
  assert.equal(bob.native.calls.create.length, 1);
});

test('escaped control text obeys 40 KiB serialized envelope limit below 48 KiB RPC limit', async t => {
  const { alice, bob, wire } = await fixture(t);
  const oversized = message({ id: 'escaped-size', text: '\u0000'.repeat(7000) + 'x' });
  assert.ok(Buffer.byteLength(oversized.text) < 16 * 1024);
  const bytes = Buffer.byteLength(JSON.stringify(oversized));
  assert.ok(bytes > 40 * 1024 && bytes < 48 * 1024);
  assert.throws(() => validateEnvelope(oversized), /Invalid DM envelope/);
  assert.throws(() => bob.host.receive(oversized, 'alice'), /Invalid DM envelope/);
  await assert.rejects(alice.host.send({ to: 'bob', text: oversized.text, messageId: oversized.id }), /Invalid DM envelope/);
  const withinLimit = message({ text: '\u0000'.repeat(6000) + 'x' });
  assert.equal(validateEnvelope(withinLimit), withinLimit);
  assert.equal(wire.length, 0);
  assert.equal(alice.native.calls.create.length, 0);
  assert.equal(bob.native.calls.create.length, 0);
});

test('stable handle and local pair survive restart/profile initial-handle change', async t => {
  const { alice, bob, restart, contacts } = await fixture(t);
  await alice.host.send({ to: 'bob', text: 'first', messageId: 'stable-1' });
  const original = await bob.host.pair('alice');
  await bob.store.setName('Robert');
  await restart(bob, 'profile-renamed');
  assert.deepEqual(bob.host.identity(), { handle: 'bob', name: 'Robert' });
  assert.deepEqual((await contacts()).find(item => item.handle === 'bob'), { handle: 'bob', name: 'Robert' });
  assert.equal((await bob.host.pair('alice')).sessionId, original.sessionId);
  assert.equal(bob.native.calls.create.length, 1);
});

test('offline retry across restart preserves same ID and injects sender context once without waking', async t => {
  const { alice, bob, wire, restart } = await fixture(t);
  bob.online = false;
  const first = await alice.host.send({ to: 'bob', text: 'later', messageId: 'offline-1' });
  assert.equal(first.status, 'pending');
  assert.equal(first.attempts, 1);
  assert.equal(first.lastError, 'Peer unavailable or delivery not yet confirmed.');
  assert.equal(JSON.stringify(first).includes('password'), false);
  const pair = await alice.host.pair('bob');
  assert.equal(alice.native.calls.inject.length, 1);
  assert.equal(alice.native.calls.followup.length, 0);
  assert.equal(alice.native.state(pair.sessionId).status, 'idle');
  await restart(alice);
  bob.online = true;
  await alice.host.pump();
  const accepted = await alice.store.getRecord('outbox', first.id);
  assert.equal(accepted.status, 'accepted');
  assert.equal(accepted.attempts, 2);
  assert.equal(Object.hasOwn(accepted, 'lastError'), false);
  await alice.host.deliver(first.id);
  await alice.host.pump();
  assert.equal(wire.length, 2);
  assert.deepEqual(wire[0], wire[1]);
  assert.equal(bob.native.calls.followup.length, 1);
  assert.equal(alice.native.calls.inject.length, 1);
  assert.equal(alice.native.calls.followup.length, 0);
  assert.equal(alice.native.state(pair.sessionId).nextStep.length, 1);
  assert.equal(alice.native.state(pair.sessionId).status, 'idle');
  await restart(alice);
  await alice.host.pump();
  assert.equal(alice.native.calls.inject.length, 1);
  assert.equal(wire.length, 2);
});

test('sender context is durably flushed before wire, including retry after failed native flush', async t => {
  const { alice, bob, wire } = await fixture(t);
  const pair = await alice.host.pair('bob');
  const seen = [];
  alice.beforeTransmit = envelope => {
    const durable = alice.native.durableState(pair.sessionId);
    assert.equal(durable.nextStep.filter(message => message.id === `dm-out-${envelope.id}`).length, 1,
      'wire delivery must never outrun the sender native context durability barrier');
    seen.push(envelope.id);
  };
  alice.native.control.failFlush = 'before';
  const pending = await alice.host.send({ to: 'bob', text: 'durable before wire', messageId: 'sender-flush-1' });
  assert.equal(pending.status, 'pending');
  assert.equal(wire.length, 0);
  assert.equal(bob.native.calls.followup.length, 0);
  assert.equal(alice.native.calls.inject.length, 1);
  assert.equal(alice.native.durableState(pair.sessionId).nextStep.length, 0);
  assert.equal(alice.native.calls.followup.length, 0);
  const accepted = await alice.host.deliver(pending.id);
  assert.equal(accepted.status, 'accepted');
  assert.deepEqual(seen, ['sender-flush-1']);
  assert.equal(wire.length, 1);
  assert.equal(bob.native.calls.followup.length, 1);
  assert.equal(alice.native.calls.inject.length, 1);
  assert.equal(alice.native.state(pair.sessionId).status, 'idle');
});

test('outgoing native user-message evidence deduplicates context across restart and retry', async t => {
  const { alice, bob, wire, restart } = await fixture(t);
  bob.online = false;
  const pending = await alice.host.send({ to: 'bob', text: 'already in local model context', messageId: 'outgoing-committed' });
  const pair = await alice.host.pair('bob');
  alice.native.commitInjected(pair.sessionId, 'dm-out-outgoing-committed');
  // A user/message can be the only surviving native admission evidence. It is
  // authoritative independently of whether an inbox splice remains in a view.
  const state = alice.native.state(pair.sessionId);
  state.events = state.events.filter(event => event.type !== 'agent/inbox/spliced');
  await alice.native.flush();
  await restart(alice);
  bob.online = true;
  const accepted = await alice.host.deliver(pending.id);
  assert.equal(accepted.status, 'accepted');
  assert.equal(alice.native.calls.inject.length, 1);
  assert.equal(alice.native.calls.followup.length, 0);
  assert.equal(alice.native.state(pair.sessionId).nextStep.length, 0);
  assert.equal(alice.native.state(pair.sessionId).events.filter(event => event.type === 'user/message').length, 1);
  assert.equal(wire.length, 2);
});

test('concurrent equal send IDs join one context injection/wire delivery; conflicting content rejects', async t => {
  const { alice, bob, wire } = await fixture(t);
  const args = { to: 'bob', text: 'one explicit message', messageId: 'joined-send-1' };
  const first = alice.host.send(args);
  const second = alice.host.send({ ...args });
  assert.equal(first, second);
  assert.throws(() => alice.host.send({ ...args, text: 'conflicting concurrent content' }), /Conflicting concurrent/);
  const results = await Promise.all([first, second]);
  assert.equal(results[0].status, 'accepted');
  assert.deepEqual(results[0], results[1]);
  assert.equal(alice.native.calls.inject.length, 1);
  assert.equal(alice.native.calls.followup.length, 0);
  assert.equal(bob.native.calls.followup.length, 1);
  assert.equal(wire.length, 1);
  await alice.host.send({ ...args });
  assert.equal(alice.native.calls.inject.length, 1);
  assert.equal(wire.length, 1);
  await assert.rejects(alice.host.send({ ...args, text: 'conflicting persisted content' }), /different content/);
});

test('duplicate acceptance has zero Agent activation, even after restart', async t => {
  const { bob, restart } = await fixture(t);
  const envelope = message();
  const ack = await bob.host.receive(envelope, 'alice');
  const count = bob.native.calls.resolve.length;
  assert.deepEqual(await bob.host.receive(envelope, 'alice'), ack);
  assert.equal(bob.native.calls.resolve.length, count);
  await restart(bob);
  assert.deepEqual(await bob.host.receive(envelope, 'alice'), ack);
  assert.equal(bob.native.calls.resolve.length, count);
  assert.equal(bob.native.calls.followup.length, 1);
});

test('sender/recipient forgery and conflicting IDs are rejected without native delivery', async t => {
  const { alice, bob } = await fixture(t);
  assert.throws(() => bob.host.receive(message(), 'mallory'), /mismatch/);
  assert.throws(() => alice.host.receive(message(), 'alice'), /mismatch/);
  assert.throws(() => bob.host.receive(message({ dmId: 'bad' }), 'alice'), /Invalid/);
  assert.throws(() => bob.host.receive(message({ extra: 'spoof' }), 'alice'), /Invalid/);
  for (const id of [123, true, null, ['letters'], { toString: () => 'letters' }]) {
    assert.throws(() => bob.host.receive(message({ id }), 'alice'), /Invalid/);
    assert.throws(() => bob.host.receive(message({ replyTo: id }), 'alice'), /Invalid/);
  }
  assert.throws(() => alice.host.send({ to: 'bob', text: 'no', messageId: 123 }), /Invalid/);
  assert.equal(bob.native.calls.followup.length, 0);
  await bob.host.receive(message(), 'alice');
  await assert.rejects(bob.host.receive(message({ text: 'changed' }), 'alice'), /different content/);
  const sent = await alice.host.send({ to: 'bob', text: 'outgoing', messageId: 'outgoing-1' });
  assert.equal(sent.status, 'accepted');
  await assert.rejects(alice.host.send({ to: 'bob', text: 'changed', messageId: 'outgoing-1' }), /different content/);
  await assert.rejects(alice.host.send({ to: 'unknown', text: 'no' }), /Unknown DM handle/);
  assert.equal(bob.native.calls.followup.length, 2);
});

test('reply correlation is limited to incoming messages from the addressed peer', async t => {
  const { alice, bob, open, wire } = await fixture(t);
  await open('carol');
  await alice.host.send({ to: 'bob', text: 'question', messageId: 'question-1' });
  const reply = await bob.host.send({ to: 'alice', text: 'answer', messageId: 'answer-1', replyTo: 'question-1' });
  assert.equal(reply.status, 'accepted');
  assert.equal(wire[1].replyTo, 'question-1');
  assert.equal(alice.native.calls.followup[0].message.content[0].text, '[MAGI-DM] from @bob:\n\nanswer');
  assert.equal(alice.native.calls.followup[0].message.source.messageId, 'answer-1');
  const local = await alice.host.pair('bob');
  assert.equal(alice.native.calls.inject[0].sessionId, local.sessionId);
  assert.equal(alice.native.calls.followup[0].sessionId, local.sessionId);
  const state = alice.native.state(local.sessionId);
  assert.equal(state.nextStep[0].id, 'dm-out-question-1');
  assert.match(state.nextStep[0].content[0].text, /\n\nquestion$/);
  assert.equal(state.inbox[0].id, 'dm-answer-1');
  assert.match(state.inbox[0].content[0].text, /\n\nanswer$/);
  alice.native.commitInjected(local.sessionId, 'dm-out-question-1');
  alice.native.commit(local.sessionId, 'dm-answer-1');
  const committed = state.events.filter(event => event.type === 'user/message');
  assert.deepEqual(committed.map(event => event.data.source.kind), ['eva-dm-outgoing', 'eva-dm']);
  assert.match(committed[0].data.content[0].text, /question$/);
  assert.match(committed[1].data.content[0].text, /answer$/);
  await assert.rejects(bob.host.send({ to: 'carol', text: 'wrong peer', replyTo: 'question-1' }), /reply_to/);
  await assert.rejects(bob.host.send({ to: 'alice', text: 'missing', replyTo: 'missing' }), /reply_to/);
  await assert.rejects(bob.host.send({ to: 'alice', text: 'own outgoing', replyTo: 'answer-1' }), /reply_to/);
  assert.equal(wire.length, 2);
});

test('two peers sharing a manual wire ID admit concurrently into separate inbox records, Sessions and reply histories', async t => {
  const { alice, bob, open, wire, restart } = await fixture(t);
  const carol = await open('carol');
  const sharedId = 'review-001';
  const results = await Promise.all([
    alice.host.send({ to: 'bob', text: 'alice question', messageId: sharedId }),
    carol.host.send({ to: 'bob', text: 'carol question', messageId: sharedId }),
  ]);
  assert.deepEqual(results.map(record => record.status), ['accepted', 'accepted']);
  assert.equal(bob.native.calls.followup.length, 2);
  const alicePair = await bob.host.pair('alice');
  const carolPair = await bob.host.pair('carol');
  assert.notEqual(alicePair.sessionId, carolPair.sessionId);
  assert.equal(bob.native.state(alicePair.sessionId).inbox[0].id, `dm-${sharedId}`);
  assert.equal(bob.native.state(carolPair.sessionId).inbox[0].id, `dm-${sharedId}`);
  assert.match(bob.native.state(alicePair.sessionId).inbox[0].content[0].text, /alice question$/);
  assert.match(bob.native.state(carolPair.sessionId).inbox[0].content[0].text, /carol question$/);
  assert.notEqual(inboxRecordId('alice', sharedId), inboxRecordId('carol', sharedId));
  for (const peer of ['alice', 'carol']) {
    const incoming = await bob.store.getRecord('inbox', inboxRecordId(peer, sharedId));
    assert.equal(incoming.id, sharedId, 'public message ID stays unchanged');
    assert.equal(incoming.from, peer);
    assert.equal(incoming.status, 'accepted');
    const history = await bob.host.history(peer);
    assert.deepEqual(history.map(record => [record.id, record.from, record.text]), [[sharedId, peer, `${peer} question`]]);
  }
  const originals = wire.slice();
  const resolves = bob.native.calls.resolve.length;
  for (const envelope of originals) {
    assert.equal((await bob.host.receive(envelope, envelope.from)).id, sharedId);
  }
  await restart(bob);
  await bob.host.pump();
  assert.equal(bob.native.calls.resolve.length, resolves, 'namespaced startup audits never re-activate accepted input');
  assert.equal(bob.native.calls.followup.length, 2);
  for (const envelope of originals) {
    assert.equal((await bob.host.receive(envelope, envelope.from)).status, 'accepted');
  }
  const replies = await Promise.all([
    bob.host.send({ to: 'alice', text: 'alice answer', messageId: 'reply-alice', replyTo: sharedId }),
    bob.host.send({ to: 'carol', text: 'carol answer', messageId: 'reply-carol', replyTo: sharedId }),
  ]);
  assert.deepEqual(replies.map(record => record.status), ['accepted', 'accepted']);
  assert.equal(wire.length, 4);
  for (const peer of ['alice', 'carol']) {
    const history = await bob.host.history(peer);
    assert.equal(history.length, 2);
    assert.deepEqual(new Set(history.map(record => record.text)), new Set([`${peer} question`, `${peer} answer`]));
    assert.equal(history.find(record => record.direction === 'outgoing').replyTo, sharedId);
  }
  assert.match(alice.native.calls.followup[0].message.content[0].text, /alice answer$/);
  assert.match(carol.native.calls.followup[0].message.content[0].text, /carol answer$/);
});

test('history/status are DM records only; native/model/tool/unrelated output is never forwarded', async t => {
  const { alice, bob, wire } = await fixture(t);
  await alice.host.send({ to: 'bob', text: 'explicit', messageId: 'history-1' });
  const pair = await bob.host.pair('alice');
  bob.native.state(pair.sessionId).events.push(
    { type: 'assistant/message', data: { text: 'private model output' } },
    { type: 'tool/result', data: { text: 'private tool output' } },
    { type: 'user/message', data: { id: 'ordinary-user', role: 'user', content: [{ type: 'text', text: 'ordinary user input' }] } },
  );
  const before = wire.length;
  const history = await bob.host.history('alice');
  assert.equal(history.length, 1);
  assert.equal(history[0].direction, 'incoming');
  assert.equal(history[0].status, 'accepted');
  assert.equal(history[0].text, 'explicit');
  const outgoing = await alice.host.history('bob', 1);
  assert.equal(outgoing[0].direction, 'outgoing');
  assert.equal(outgoing[0].status, 'accepted');
  assert.equal(outgoing[0].id, history[0].id);
  assert.equal(JSON.stringify(history).includes('private'), false);
  await bob.host.pump();
  assert.equal(wire.length, before);
  await assert.rejects(bob.host.history('alice', 0), /limit/);
  await assert.rejects(bob.host.history('alice', 51), /limit/);
});

test('concurrent duplicate receive shares one admission; conflicting concurrent content fails', async t => {
  const { bob } = await fixture(t);
  const envelope = message({ id: 'concurrent-1' });
  const first = bob.host.receive(envelope, 'alice');
  const second = bob.host.receive({ ...envelope }, 'alice');
  assert.equal(first, second);
  assert.throws(() => bob.host.receive({ ...envelope, text: 'conflict' }, 'alice'), /Conflicting concurrent/);
  const results = await Promise.all([first, second]);
  assert.deepEqual(results[0], results[1]);
  assert.equal(bob.native.calls.followup.length, 1);
});

test('native flush failure before disk leaves received intent; restart safely admits once', async t => {
  const { bob, restart } = await fixture(t);
  const pair = await bob.host.pair('alice');
  bob.native.control.failFlush = 'before';
  await assert.rejects(bob.host.receive(message({ id: 'flush-before' }), 'alice'), /flush before disk/);
  assert.equal((await bob.store.getRecord('inbox', inboxRecordId('alice', 'flush-before'))).status, 'received');
  await restart(bob);
  assert.equal(bob.native.state(pair.sessionId).inbox.length, 0);
  await bob.host.pump();
  assert.equal((await bob.store.getRecord('inbox', inboxRecordId('alice', 'flush-before'))).status, 'accepted');
  assert.equal(bob.native.state(pair.sessionId).inbox.length, 1);
  assert.equal(nativeAdmission(bob.native.state(pair.sessionId).events, message({ id: 'flush-before' })), 'pending');
  bob.native.commit(pair.sessionId, 'dm-flush-before');
  await bob.native.flush();
  const followups = bob.native.calls.followup.length;
  await restart(bob);
  await bob.host.pump();
  assert.equal(bob.native.calls.followup.length, followups);
  assert.equal(bob.native.state(pair.sessionId).events.filter(event => event.type === 'user/message').length, 1);
});

test('ambiguous native flush crash gap deduplicates already committed model input', async t => {
  const { bob, restart } = await fixture(t);
  const pair = await bob.host.pair('alice');
  const originalFlush = bob.native.ctx.sessionPersistence.flush;
  bob.native.ctx.sessionPersistence.flush = async () => {
    bob.native.commit(pair.sessionId, 'dm-flush-after');
    await originalFlush();
    throw new Error('ambiguous native flush completion');
  };
  await assert.rejects(bob.host.receive(message({ id: 'flush-after' }), 'alice'), /ambiguous/);
  bob.native.ctx.sessionPersistence.flush = originalFlush;
  assert.equal((await bob.store.getRecord('inbox', inboxRecordId('alice', 'flush-after'))).status, 'received');
  const resolves = bob.native.calls.resolve.length;
  await restart(bob);
  await bob.host.pump();
  assert.equal((await bob.store.getRecord('inbox', inboxRecordId('alice', 'flush-after'))).status, 'accepted');
  assert.equal(bob.native.calls.followup.length, 1);
  assert.equal(bob.native.calls.resolve.length, resolves);
  assert.equal(bob.native.state(pair.sessionId).events.filter(event => event.type === 'user/message').length, 1);
});

test('startup audits accepted pending input without activation, prompt, removal, or reinsertion', async t => {
  const { bob, restart } = await fixture(t);
  const envelope = message({ id: 'pending-restart' });
  await bob.host.receive(envelope, 'alice');
  const pair = await bob.host.pair('alice');
  const original = clone(bob.native.state(pair.sessionId));
  const resolves = bob.native.calls.resolve.length;
  await restart(bob);
  await bob.host.pump();
  await bob.host.pump();
  const state = bob.native.state(pair.sessionId);
  assert.deepEqual(state.events, original.events);
  assert.deepEqual(state.inbox, original.inbox);
  assert.equal(state.status, 'idle');
  assert.equal(bob.native.calls.followup.length, 1);
  assert.equal(bob.native.calls.resolve.length, resolves);
  assert.equal(bob.native.calls.remove.length, 0);
  const record = await bob.store.getRecord('inbox', inboxRecordId(envelope.from, envelope.id));
  assert.equal(record.status, 'accepted', 'accepted is durable admission, not model execution');
  assert.equal(record.attention, 'native-inbox-pending-after-restart');
  assert.equal(Object.hasOwn(record, 'recovery'), false);
  assert.deepEqual(await bob.host.receive(envelope, 'alice'), { id: envelope.id, dmId: envelope.dmId, status: 'accepted' });
  assert.equal(bob.native.calls.resolve.length, resolves);
  assert.equal(bob.native.calls.followup.length, 1);
});

test('claimed-before-user-commit fails closed with attention and never requeues or wakes', async t => {
  const { bob, restart } = await fixture(t);
  const envelope = message({ id: 'claimed-restart' });
  await bob.host.receive(envelope, 'alice');
  const pair = await bob.host.pair('alice');
  bob.native.claim(pair.sessionId, 'dm-claimed-restart');
  await bob.native.flush();
  assert.equal(nativeAdmission(bob.native.state(pair.sessionId).events, envelope), 'claimed');
  const events = clone(bob.native.state(pair.sessionId).events);
  const resolves = bob.native.calls.resolve.length;
  const removes = bob.native.calls.remove.length;
  await restart(bob);
  await bob.host.pump();
  assert.equal(bob.native.calls.followup.length, 1);
  assert.equal(bob.native.calls.resolve.length, resolves);
  assert.equal(bob.native.calls.remove.length, removes);
  assert.deepEqual(bob.native.state(pair.sessionId).events, events);
  assert.equal(bob.native.state(pair.sessionId).inbox.length, 0);
  assert.equal(nativeAdmission(bob.native.state(pair.sessionId).events, envelope), 'claimed');
  const record = await bob.store.getRecord('inbox', inboxRecordId(envelope.from, envelope.id));
  assert.equal(record.status, 'accepted');
  assert.equal(record.attention, 'native-input-consumed-before-model-commit');
});

test('committed or user-canceled native input is never re-admitted, activated, or marked for retry', async t => {
  for (const outcome of ['committed', 'canceled']) {
    const { bob, restart } = await fixture(t);
    const envelope = message({ id: `final-${outcome}` });
    await bob.host.receive(envelope, 'alice');
    const pair = await bob.host.pair('alice');
    if (outcome === 'committed') bob.native.commit(pair.sessionId, `dm-${envelope.id}`);
    else bob.native.cancel(pair.sessionId, `dm-${envelope.id}`);
    await bob.native.flush();
    const resolves = bob.native.calls.resolve.length;
    const removes = bob.native.calls.remove.length;
    const events = clone(bob.native.state(pair.sessionId).events);
    await restart(bob);
    await bob.host.pump();
    assert.equal(nativeAdmission(bob.native.state(pair.sessionId).events, envelope), outcome);
    assert.equal(bob.native.calls.followup.length, 1);
    assert.equal(bob.native.calls.resolve.length, resolves);
    assert.equal(bob.native.calls.remove.length, removes);
    assert.deepEqual(bob.native.state(pair.sessionId).events, events);
    assert.equal(bob.native.state(pair.sessionId).inbox.length, 0);
    assert.equal(Object.hasOwn(await bob.store.getRecord('inbox', inboxRecordId(envelope.from, envelope.id)), 'attention'), false);
    assert.deepEqual(await bob.host.receive(envelope, 'alice'), { id: envelope.id, dmId: envelope.dmId, status: 'accepted' });
    assert.equal(bob.native.calls.resolve.length, resolves);
    assert.equal(bob.native.calls.followup.length, 1);
  }
});

test('accepted record missing native admission evidence fails closed rather than inventing new input', async t => {
  const { bob, restart } = await fixture(t);
  const envelope = message({ id: 'missing-native-evidence' });
  await bob.host.receive(envelope, 'alice');
  const pair = await bob.host.pair('alice');
  const state = bob.native.state(pair.sessionId);
  state.events = [];
  state.inbox = [];
  await bob.native.flush();
  const resolves = bob.native.calls.resolve.length;
  await restart(bob);
  await bob.host.pump();
  const record = await bob.store.getRecord('inbox', inboxRecordId(envelope.from, envelope.id));
  assert.equal(record.status, 'accepted');
  assert.equal(record.attention, 'native-admission-evidence-missing');
  assert.equal(bob.native.calls.resolve.length, resolves);
  assert.equal(bob.native.calls.followup.length, 1);
  assert.equal(bob.native.calls.remove.length, 0);
  assert.equal(bob.native.state(pair.sessionId).events.length, 0);
  assert.equal(bob.native.state(pair.sessionId).inbox.length, 0);
});

test('ambiguous pending native flush crash preserves historic insertion and acks without waking', async t => {
  const { bob, restart } = await fixture(t);
  const envelope = message({ id: 'pending-flush-gap' });
  const pair = await bob.host.pair('alice');
  bob.native.control.failFlush = 'after';
  await assert.rejects(bob.host.receive(envelope, 'alice'), /ambiguous after disk/);
  assert.equal((await bob.store.getRecord('inbox', inboxRecordId(envelope.from, envelope.id))).status, 'received');
  const events = clone(bob.native.state(pair.sessionId).events);
  const resolves = bob.native.calls.resolve.length;
  await restart(bob);
  await bob.host.pump();
  const record = await bob.store.getRecord('inbox', inboxRecordId(envelope.from, envelope.id));
  assert.equal(record.status, 'accepted');
  assert.equal(record.attention, 'native-inbox-pending-after-restart');
  assert.equal(bob.native.calls.resolve.length, resolves);
  assert.equal(bob.native.calls.followup.length, 1);
  assert.equal(bob.native.calls.remove.length, 0);
  assert.deepEqual(bob.native.state(pair.sessionId).events, events);
  assert.equal(bob.native.state(pair.sessionId).inbox.length, 1);
});

test('historic claimed or canceled admission in an unacknowledged crash gap is never reactivated', async t => {
  for (const outcome of ['claimed', 'canceled']) {
    const { bob, restart } = await fixture(t);
    const envelope = message({ id: `unacked-${outcome}` });
    const pair = await bob.host.pair('alice');
    const originalFlush = bob.native.ctx.sessionPersistence.flush;
    bob.native.ctx.sessionPersistence.flush = async () => {
      if (outcome === 'claimed') bob.native.claim(pair.sessionId, `dm-${envelope.id}`);
      else bob.native.cancel(pair.sessionId, `dm-${envelope.id}`);
      await originalFlush();
      throw new Error('native durable but ack not persisted');
    };
    await assert.rejects(bob.host.receive(envelope, 'alice'), /ack not persisted/);
    bob.native.ctx.sessionPersistence.flush = originalFlush;
    const resolves = bob.native.calls.resolve.length;
    const removes = bob.native.calls.remove.length;
    const events = clone(bob.native.state(pair.sessionId).events);
    await restart(bob);
    await bob.host.pump();
    const record = await bob.store.getRecord('inbox', inboxRecordId(envelope.from, envelope.id));
    assert.equal(record.status, 'accepted');
    if (outcome === 'claimed') assert.equal(record.attention, 'native-input-consumed-before-model-commit');
    else assert.equal(Object.hasOwn(record, 'attention'), false);
    assert.equal(bob.native.calls.resolve.length, resolves);
    assert.equal(bob.native.calls.followup.length, 1);
    assert.equal(bob.native.calls.remove.length, removes);
    assert.equal(bob.native.state(pair.sessionId).inbox.length, 0);
    assert.deepEqual(bob.native.state(pair.sessionId).events, events);
  }
});

test('cancel/commit racing an awaited startup audit never activates or replaces native input', { timeout: 5000 }, async t => {
  for (const outcome of ['canceled', 'committed']) {
    const { bob, restart } = await fixture(t);
    const envelope = message({ id: `audit-race-${outcome}` });
    await bob.host.receive(envelope, 'alice');
    const pair = await bob.host.pair('alice');
    await restart(bob);
    const originalInspect = bob.native.ctx.sessionController.inspect;
    let release;
    let markEntered;
    const entered = new Promise(resolve => { markEntered = resolve; });
    const gate = new Promise(resolve => { release = resolve; });
    bob.native.ctx.sessionController.inspect = async id => {
      const stale = await originalInspect(id);
      markEntered();
      await gate;
      return stale;
    };
    const resolves = bob.native.calls.resolve.length;
    const pumping = bob.host.pump();
    await entered;
    if (outcome === 'canceled') bob.native.cancel(pair.sessionId, `dm-${envelope.id}`);
    else bob.native.commit(pair.sessionId, `dm-${envelope.id}`);
    await bob.native.flush();
    const events = clone(bob.native.state(pair.sessionId).events);
    const removes = bob.native.calls.remove.length;
    release();
    await pumping;
    bob.native.ctx.sessionController.inspect = originalInspect;
    assert.equal(bob.native.calls.resolve.length, resolves);
    assert.equal(bob.native.calls.followup.length, 1);
    assert.equal(bob.native.calls.remove.length, removes);
    assert.equal(bob.native.state(pair.sessionId).inbox.length, 0);
    assert.deepEqual(bob.native.state(pair.sessionId).events, events);
    assert.equal(nativeAdmission(events, envelope), outcome);
  }
});

test('next substantive peer DM queues naturally beside preserved pending input with no synthetic prompt', async t => {
  const { bob, restart } = await fixture(t);
  const first = message({ id: 'prior-pending' });
  await bob.host.receive(first, 'alice');
  const pair = await bob.host.pair('alice');
  await restart(bob);
  await bob.host.pump();
  assert.equal(bob.native.calls.followup.length, 1);
  await bob.host.receive(message({ id: 'new-real-input', text: 'actual new peer content', createdAt: 101 }), 'alice');
  assert.equal(bob.native.calls.followup.length, 2);
  assert.deepEqual(bob.native.calls.followup.map(call => call.message.id), ['dm-prior-pending', 'dm-new-real-input']);
  assert.ok(bob.native.calls.followup.every(call => call.message.source.kind === 'eva-dm'));
  assert.deepEqual(bob.native.state(pair.sessionId).inbox.map(item => item.id), ['dm-prior-pending', 'dm-new-real-input']);
  assert.equal(bob.native.calls.remove.length, 0);
  bob.native.commit(pair.sessionId, 'dm-prior-pending');
  bob.native.commit(pair.sessionId, 'dm-new-real-input');
  await bob.native.flush();
  await restart(bob);
  await bob.host.pump();
  assert.equal(bob.native.calls.followup.length, 2);
  assert.equal(bob.native.state(pair.sessionId).events.filter(event => event.type === 'user/message').length, 2);
  assert.equal(Object.hasOwn(await bob.store.getRecord('inbox', inboxRecordId(first.from, first.id)), 'attention'), false);
});
