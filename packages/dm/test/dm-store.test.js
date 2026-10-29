import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, realpath, rm, readFile, writeFile, lstat, mkdir, symlink, unlink, chmod, link, rename } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { DmStore, dmIdFor } from '../dm-store.js';

async function fixture(t) {
  const base = await realpath(tmpdir());
  const profile = await mkdtemp(join(base, 'magi-dm-test-'));
  t.after(async () => {
    assert.equal(resolve(profile), profile);
    assert.ok(profile.startsWith(join(base, 'magi-dm-test-')));
    await rm(profile, { recursive: true, force: true });
  });
  return { profile, root: join(profile, 'magi-dm'), store: await DmStore.open(profile, 'alice') };
}
const readJson = async path => JSON.parse(await readFile(path, 'utf8'));
const privateJson = (path, value) => writeFile(path, JSON.stringify(value), { mode: 0o600 });

test('identity is private, cloned, durable, and retains handle on profile rename', async t => {
  const { profile, root, store } = await fixture(t);
  assert.deepEqual(store.identity(), { handle: 'alice', name: 'alice' });
  const copy = store.identity(); copy.handle = 'evil';
  assert.equal(store.identity().handle, 'alice');
  assert.deepEqual(await store.setName('  Alice Example  '), { handle: 'alice', name: 'Alice Example' });
  for (const child of ['', '/pairs', '/inbox', '/outbox', '/identity.json']) {
    assert.equal((await lstat(`${root}${child}`)).mode & 0o077, 0);
  }
  assert.deepEqual((await DmStore.open(profile, 'renamed')).identity(), store.identity());
  // Moving the profile directory does not bind identity to its path or new handle.
  const moved = `${profile}/renamed-profile`;
  await mkdir(moved, { mode: 0o700 });
  await rename(root, join(moved, 'magi-dm'));
  assert.deepEqual((await DmStore.open(moved, 'renamed')).identity(), store.identity());
});

test('name validation trims, bounds, rejects control characters, and leaves identity intact', async t => {
  const { store } = await fixture(t);
  for (const name of ['', '   ', null, 7, 'a'.repeat(81), 'a\nb', 'a\tb', 'x\u0000', 'x\u007f', 'x\u0085']) {
    await assert.rejects(store.setName(name), /Invalid DM name/);
  }
  await store.setName('a'.repeat(80));
  assert.equal(store.identity().name.length, 80);
  await store.setName('😀'.repeat(80));
  assert.equal([...store.identity().name].length, 80);
});

test('dm IDs are symmetric SHA256 of sorted handles and reject invalid/self peers', async t => {
  const { store } = await fixture(t);
  const expected = createHash('sha256').update(JSON.stringify(['alice', 'bob'])).digest('hex');
  assert.equal(dmIdFor('alice', 'bob'), expected);
  assert.equal(dmIdFor('bob', 'alice'), expected);
  for (const peer of ['alice', '', '../bob', 'Bob', 'b'.repeat(33), null, {}, 'bob\n']) {
    await assert.rejects(store.pair(peer, () => assert.fail('invalid peer must not create')), /Invalid DM handle|itself/);
  }
  assert.throws(() => dmIdFor('alice', 'alice'), /itself/);
  await assert.rejects(store.pair('bob'), /requires createSession/);
});

test('concurrent pairs reserve before create, create once, and persist the exact ready ID', async t => {
  const { profile, root, store } = await fixture(t);
  let calls = 0;
  const create = async sessionId => {
    calls++;
    assert.match(sessionId, /^session-[0-9a-f-]{36}$/);
    const reservation = await readJson(join(root, 'pairs', `${dmIdFor('alice', 'bob')}.json`));
    assert.equal(reservation.state, 'reserved');
    assert.equal(reservation.sessionId, sessionId);
    await new Promise(resolve => setImmediate(resolve));
  };
  const results = await Promise.all(Array.from({ length: 20 }, () => store.pair('bob', create)));
  assert.equal(calls, 1);
  assert.ok(results.every(value => JSON.stringify(value) === JSON.stringify(results[0])));
  assert.deepEqual(Object.keys(results[0]).sort(), ['dmId', 'peer', 'sessionId']);
  assert.equal((await readJson(join(root, 'pairs', `${results[0].dmId}.json`))).state, 'ready');
  const reopened = await DmStore.open(profile, 'renamed');
  assert.deepEqual(await reopened.pair('bob', () => assert.fail('must never replace missing native Session')), results[0]);
  results[0].peer = 'tampered';
  assert.equal((await store.pair('bob', create)).peer, 'bob');
});

test('an interrupted create retains its reservation and fails closed on retry/reopen', async t => {
  const { profile, root, store } = await fixture(t);
  let id;
  await assert.rejects(store.pair('bob', async sessionId => { id = sessionId; throw new Error('native create interrupted'); }), /native create interrupted/);
  const reservation = await readJson(join(root, 'pairs', `${dmIdFor('alice', 'bob')}.json`));
  assert.equal(reservation.sessionId, id);
  assert.equal(reservation.state, 'reserved');
  for (const instance of [store, await DmStore.open(profile, 'alice')]) {
    await assert.rejects(instance.pair('bob', () => assert.fail('must not retry')), error => {
      assert.match(error.message, /Incomplete DM reservation.*manual recovery/);
      assert.ok(error.message.includes(id)); return true;
    });
  }
  await store.setName('Still usable');
  await store.putRecord('outbox', 'ok', { status: 'pending' });
  assert.equal((await store.getRecord('outbox', 'ok')).status, 'pending');
});

test('records are cloned durable objects, atomically updated and sorted by createdAt/id', async t => {
  const { profile, store } = await fixture(t);
  assert.equal(await store.getRecord('inbox', 'missing'), undefined);
  const value = { id: 'z', createdAt: '2026-10-01', nested: { text: 'hello' } };
  const putting = store.putRecord('outbox', 'z', value);
  value.nested.text = 'mutated';
  await putting;
  await Promise.all([
    store.putRecord('outbox', 'c', { id: 'c', createdAt: '2026-10-02' }),
    store.putRecord('outbox', 'b', { id: 'b', createdAt: '2026-10-01' }),
    store.setName('Concurrent name'),
    store.putRecord('inbox', 'separate', { id: 'separate' }),
  ]);
  assert.deepEqual((await store.records('outbox')).map(item => item.id), ['b', 'z', 'c']);
  const returned = await store.getRecord('outbox', 'z'); returned.nested.text = 'changed';
  assert.equal((await store.getRecord('outbox', 'z')).nested.text, 'hello');
  await Promise.all(Array.from({ length: 20 }, (_, i) => store.putRecord('outbox', 'z', { index: i })));
  assert.deepEqual(await store.getRecord('outbox', 'z'), { index: 19 });
  const reopened = await DmStore.open(profile, 'other');
  assert.deepEqual(await reopened.getRecord('outbox', 'z'), { index: 19 });
  assert.equal(reopened.identity().name, 'Concurrent name');
});

test('invalid paths, direction, IDs, non-JSON and oversized records never write', async t => {
  const { profile, store } = await fixture(t);
  for (const handle of ['', 'ALICE', '../alice', 'a'.repeat(33), null]) await assert.rejects(DmStore.open(profile, handle), /Invalid DM handle/);
  await assert.rejects(DmStore.open('relative', 'alice'), /absolute and canonical/);
  await assert.rejects(DmStore.open(`${profile}/../${profile.split('/').at(-1)}`, 'alice'), /absolute and canonical/);
  for (const direction of ['pairs', '../inbox', null]) {
    await assert.rejects(store.getRecord(direction, 'ok'), /direction/);
    await assert.rejects(store.putRecord(direction, 'ok', {}), /direction/);
    await assert.rejects(store.records(direction), /direction/);
  }
  for (const id of ['', '..', '../x', 'a/b', 'x'.repeat(101), 'x.json', null, undefined, 'x\n']) {
    await assert.rejects(store.getRecord('inbox', id), /record id/);
    await assert.rejects(store.putRecord('inbox', id, {}), /record id/);
  }
  const cyclic = {}; cyclic.self = cyclic;
  const extraArray = []; extraArray.extra = true;
  for (const record of [[], null, 'text', new Date(), { x: undefined }, { x: NaN }, { x: Infinity }, { x: 1n }, { x: new Date() }, { x: extraArray }, cyclic,
    { get x() { assert.fail('getter must not execute'); } }, { x: [,,] }]) {
    await assert.rejects(store.putRecord('inbox', 'bad', record), /plain JSON|sparse arrays/);
  }
  await assert.rejects(store.putRecord('inbox', 'huge', { text: 'x'.repeat(128 * 1024) }), /128KiB/);
  assert.equal(await store.getRecord('inbox', 'huge'), undefined);
});

test('symlinked profile/root/ancestor and insecure directories are refused', async t => {
  const { profile, root } = await fixture(t);
  const alias = join(profile, 'profile-alias'); await symlink(profile, alias);
  await assert.rejects(DmStore.open(alias, 'alice'), /Unsafe DM directory/);
  const nested = join(profile, 'nested'); await mkdir(nested, { mode: 0o700 });
  await symlink(root, join(nested, 'magi-dm'));
  await assert.rejects(DmStore.open(nested, 'alice'), /Unsafe DM directory/);
  await chmod(root, 0o755);
  await assert.rejects(DmStore.open(profile, 'alice'), /Unsafe DM directory/);
});

test('symlinked identity, pair and record files are refused without touching targets', async t => {
  const { profile, root, store } = await fixture(t);
  const target = join(profile, 'untouched.json'); await privateJson(target, { untouched: true });
  const record = join(root, 'outbox', 'evil.json'); await symlink(target, record);
  await assert.rejects(store.getRecord('outbox', 'evil'), /Unsafe DM state/);
  await assert.rejects(store.putRecord('outbox', 'evil', { replaced: true }), /Unsafe DM state/);
  await assert.rejects(store.records('outbox'), /Unsafe DM state/);
  await symlink(target, join(root, 'pairs', `${dmIdFor('alice', 'bob')}.json`));
  await assert.rejects(store.pair('bob', () => assert.fail('unsafe')), /Unsafe DM state/);
  await unlink(join(root, 'identity.json')); await symlink(target, join(root, 'identity.json'));
  await assert.rejects(DmStore.open(profile, 'alice'), /Unsafe DM state/);
  await assert.rejects(store.setName('new'), /Unsafe DM state/);
  assert.deepEqual(await readJson(target), { untouched: true });
});

test('symlinked storage directories and ancestors are refused after open', async t => {
  const { profile, root, store } = await fixture(t);
  const outside = join(profile, 'outside'); await mkdir(outside, { mode: 0o700 });
  const inbox = join(root, 'inbox');
  assert.equal(resolve(inbox), join(profile, 'magi-dm', 'inbox'));
  await rm(inbox, { recursive: true }); await symlink(outside, inbox);
  await assert.rejects(store.getRecord('inbox', 'id'), /Unsafe DM directory/);
  await assert.rejects(store.putRecord('inbox', 'id', {}), /Unsafe DM directory/);
  await assert.rejects(store.records('inbox'), /Unsafe DM directory/);
  await assert.rejects(DmStore.open(profile, 'alice'), /Unsafe DM directory/);
});

test('corrupt identity, pair mappings and records fail closed rather than replace', async t => {
  const { profile, root, store } = await fixture(t);
  const pair = join(root, 'pairs', `${dmIdFor('alice', 'bob')}.json`);
  await privateJson(pair, { dmId: dmIdFor('alice', 'bob'), peer: 'bob', state: 'ready', sessionId: 'not-a-session' });
  await assert.rejects(store.pair('bob', () => assert.fail('corrupt')), /Corrupt DM pair/);
  const record = join(root, 'inbox', 'broken.json');
  await writeFile(record, '{bad json', { mode: 0o600 });
  await assert.rejects(store.getRecord('inbox', 'broken'), /corrupt DM state/);
  await assert.rejects(store.putRecord('inbox', 'broken', {}), /corrupt DM state/);
  await assert.rejects(store.records('inbox'), /corrupt DM state/);
  assert.equal(await readFile(record, 'utf8'), '{bad json');
  for (const value of [{ handle: 'alice', name: '' }, { handle: 'BAD', name: 'Bad' }, [], { handle: 'alice', name: 'Alice', extra: true }]) {
    await privateJson(join(root, 'identity.json'), value);
    await assert.rejects(DmStore.open(profile, 'alice'), /Corrupt DM identity|Invalid DM|plain JSON|corrupt DM state/);
  }
  await writeFile(join(root, 'identity.json'), 'not json');
  await assert.rejects(store.setName('repair?'), /corrupt DM state/);
});

test('hardlinked, public, oversized and invalid-UTF8 files are rejected', async t => {
  const { profile, root, store } = await fixture(t);
  const dir = join(root, 'inbox');
  await privateJson(join(dir, 'public.json'), {}); await chmod(join(dir, 'public.json'), 0o644);
  await assert.rejects(store.getRecord('inbox', 'public'), /Unsafe DM state/);
  const source = join(profile, 'source.json'); await privateJson(source, {});
  await link(source, join(dir, 'hard.json'));
  await assert.rejects(store.getRecord('inbox', 'hard'), /Unsafe DM state/);
  await privateJson(join(dir, 'large.json'), { text: 'x'.repeat(128 * 1024) });
  await assert.rejects(store.getRecord('inbox', 'large'), /corrupt DM state/);
  await writeFile(join(dir, 'utf8.json'), Buffer.from([123, 34, 120, 34, 58, 34, 255, 34, 125]), { mode: 0o600 });
  await assert.rejects(store.getRecord('inbox', 'utf8'), /corrupt DM state/);
});

test('missing identity with existing state and external identity changes are refused', async t => {
  const { profile, root, store } = await fixture(t);
  await store.putRecord('outbox', 'saved', { text: 'keep' });
  await privateJson(join(root, 'identity.json'), { handle: 'mallory', name: 'Mallory' });
  await assert.rejects(store.setName('do not adopt'), /changed externally/);
  await assert.rejects(store.pair('bob', () => assert.fail('changed identity')), /changed externally/);
  await unlink(join(root, 'identity.json'));
  await assert.rejects(DmStore.open(profile, 'new-owner'), /Missing DM identity/);
});

test('an interrupted identity write is not permission to adopt a new handle', async t => {
  const { profile, root } = await fixture(t);
  const identity = join(root, 'identity.json');
  await rename(identity, join(root, '.dm-00000000-0000-4000-8000-000000000000.tmp'));
  await assert.rejects(DmStore.open(profile, 'renamed'), /Missing DM identity.*manual recovery/);
});

test('independent stores do not share a global mutation lock', async t => {
  const first = await fixture(t), second = await fixture(t);
  await first.store.pair('bob', async () => {
    await second.store.setName('Independent');
    await second.store.putRecord('inbox', 'nested', { ok: true });
  });
  assert.equal(second.store.identity().name, 'Independent');
});
