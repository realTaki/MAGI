import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, readFile, writeFile, lstat, symlink, rm, unlink, readdir, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { ensureMainSession } from '../main-session.js';

async function fixture(t) {
  const base = await mkdtemp(join(await realpath(tmpdir()), 'eva-main-test-'));
  const workspace = join(base, 'workspace');
  const stateDir = join(base, 'eva');
  await mkdir(workspace);
  await mkdir(stateDir, { mode: 0o700 });
  t.after(async () => {
    assert.equal(resolve(base), base);
    assert.ok(base.startsWith(join(await realpath(tmpdir()), 'eva-main-test-')));
    await rm(base, { recursive: true });
  });
  const options = { workspace, stateDir, profile: 'eva-000' };
  const marker = join(stateDir, 'main-session.json');
  return { base, options, marker };
}

function mock(options, stored = new Map()) {
  const calls = [];
  const durable = new Map([...stored].map(([id, value]) => [id, structuredClone(value)]));
  const ctx = {
    sessionController: {
      async create(request) {
        calls.push(['create', request]);
        assert.equal(await mappingState(), 'reserved');
        assert.equal(stored.has(request.sessionId), false);
        stored.set(request.sessionId, { meta: { id: request.sessionId, cwd: request.cwd, isSeeded: false }, events: [] });
        return { sessionId: request.sessionId, agentPreset: 'user-saved-default' };
      },
      async inspect(id) {
        calls.push(['inspect', id]);
        if (!stored.has(id)) throw Object.assign(new Error('not found'), { code: 'session/not-found' });
        return structuredClone(stored.get(id));
      },
      async resolveAgent(id) {
        calls.push(['resolveAgent', id]);
        if (!stored.has(id)) return { error: new Error('missing') };
        return { agent: { id } };
      },
    },
    workingDirectory: {
      async set(agent, path) {
        calls.push(['set', agent.id, path]);
        const session = stored.get(agent.id);
        if ((session.effectiveCwd ?? session.meta.cwd) !== path) {
          session.events.push({ type: 'working-directory/changed', data: { path } });
          session.effectiveCwd = path;
        }
        return path;
      },
    },
    sessionPersistence: {
      async flush() {
        calls.push(['flush']);
        for (const [id, value] of stored) durable.set(id, structuredClone(value));
      },
    },
  };
  async function mappingState() { return JSON.parse(await readFile(join(options.stateDir, 'main-session.json'), 'utf8')).state; }
  return { ctx, calls, stored, durable };
}

async function seed(marker, options, extra = {}) {
  const value = { version: 1, state: 'ready', profile: options.profile, workspace: options.workspace,
    sessionId: 'session-test-main', ...extra };
  await writeFile(marker, JSON.stringify(value), { mode: 0o600 });
  return value;
}

test('creates through official controller, drains persistence before ready, and preserves other Sessions', async (t) => {
  const { options, marker } = await fixture(t);
  const other = { meta: { id: 'session-other', cwd: '/other', isSeeded: false }, events: [{ content: 'untouched' }] };
  const { ctx, calls, stored, durable } = mock(options, new Map([['session-other', other]]));
  const flush = ctx.sessionPersistence.flush;
  ctx.sessionPersistence.flush = async () => {
    assert.equal(JSON.parse(await readFile(marker, 'utf8')).state, 'reserved');
    await flush();
  };
  const result = await ensureMainSession(ctx, options);
  assert.deepEqual(calls.map(([name]) => name), ['create', 'inspect', 'flush']);
  assert.deepEqual(calls[0][1], { cwd: options.workspace, sessionId: result.sessionId });
  assert.deepEqual(stored.get('session-other'), other);
  assert.ok(durable.has(result.sessionId));
  assert.equal(JSON.parse(await readFile(marker, 'utf8')).state, 'ready');
  assert.equal((await lstat(marker)).mode & 0o777, 0o600);
  assert.deepEqual(await readdir(options.stateDir), ['main-session.json']);
});

test('coalesces calls and repeated ensures never invoke create even after a turn', async (t) => {
  const { options } = await fixture(t);
  const { ctx, stored, calls } = mock(options);
  const first = ensureMainSession(ctx, options);
  const second = ensureMainSession(ctx, options);
  assert.equal(first, second);
  const result = await first;
  stored.get(result.sessionId).events.push({ type: 'turn/start' });
  assert.deepEqual(await ensureMainSession(ctx, options), result);
  assert.equal(calls.filter(([name]) => name === 'create').length, 1);
});

test('new context after simulated storage restart inspects same durable nonblank main', async (t) => {
  const { options } = await fixture(t);
  const first = mock(options);
  const result = await ensureMainSession(first.ctx, options);
  first.durable.get(result.sessionId).events.push({ type: 'turn/start' });
  const restarted = mock(options, first.durable);
  assert.deepEqual(await ensureMainSession(restarted.ctx, options), result);
  assert.deepEqual(restarted.calls.map(([name]) => name), ['inspect', 'flush']);
});

test('separate Node processes retain main identity and do not create a second Session', async (t) => {
  const { options, base } = await fixture(t);
  const moduleURL = new URL('../main-session.js', import.meta.url).href;
  const script = `
    import { readFile, writeFile } from 'node:fs/promises';
    import { ensureMainSession } from ${JSON.stringify(moduleURL)};
    const options = ${JSON.stringify(options)};
    const database = ${JSON.stringify(join(base, 'sessions.json'))};
    let records = JSON.parse(await readFile(database, 'utf8').catch(e => { if (e.code === 'ENOENT') return '{}'; throw e; }));
    const ctx = {
      sessionController: {
        async create(r) { records[r.sessionId] = {meta:{id:r.sessionId,cwd:r.cwd,isSeeded:false},events:[{type:'turn/start'}]}; return {sessionId:r.sessionId}; },
        async inspect(id) { if (!records[id]) throw Error('missing'); return records[id]; }
      },
      sessionPersistence: { async flush() { await writeFile(database, JSON.stringify(records)); } }
    };
    console.log(JSON.stringify(await ensureMainSession(ctx, options)));
  `;
  const run = () => {
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8' });
    assert.equal(child.status, 0, child.stderr);
    return JSON.parse(child.stdout);
  };
  assert.deepEqual(run(), run());
  assert.equal(Object.keys(JSON.parse(await readFile(join(base, 'sessions.json'), 'utf8'))).length, 1);
});

test('recovers reserved marker after creation or flush interruption without another create', async (t) => {
  const { options, marker } = await fixture(t);
  const first = mock(options);
  first.ctx.sessionPersistence.flush = async () => { throw new Error('disk full'); };
  await assert.rejects(ensureMainSession(first.ctx, options), /disk full/);
  assert.equal(JSON.parse(await readFile(marker, 'utf8')).state, 'reserved');
  const restarted = mock(options, first.stored);
  await ensureMainSession(restarted.ctx, options);
  assert.equal(JSON.parse(await readFile(marker, 'utf8')).state, 'ready');
  assert.deepEqual(restarted.calls.map(([name]) => name), ['inspect', 'flush']);
});

for (const state of ['ready', 'reserved']) {
  test(`refuses missing ${state} main instead of replacing a deleted or ambiguous Session`, async (t) => {
    const { options, marker } = await fixture(t);
    await seed(marker, options, { state });
    const { ctx, calls } = mock(options);
    await assert.rejects(ensureMainSession(ctx, options), /refusing replacement/);
    assert.deepEqual(calls.map(([name]) => name), ['inspect']);
    assert.equal(JSON.parse(await readFile(marker, 'utf8')).state, state);
  });
}

test('creation failure leaves reservation and subsequent calls fail closed', async (t) => {
  const { options, marker } = await fixture(t);
  const { ctx, calls } = mock(options);
  ctx.sessionController.create = async () => { throw new Error('create failed'); };
  await assert.rejects(ensureMainSession(ctx, options), /create failed/);
  assert.equal(JSON.parse(await readFile(marker, 'utf8')).state, 'reserved');
  await assert.rejects(ensureMainSession(ctx, options), /refusing replacement/);
  assert.deepEqual(calls.map(([name]) => name), ['inspect']);
});

test('refuses alternate identity returned by create without adopting it', async (t) => {
  const { options, marker } = await fixture(t);
  const { ctx } = mock(options);
  ctx.sessionController.create = async () => ({ sessionId: 'session-unexpected' });
  await assert.rejects(ensureMainSession(ctx, options), /different main Session identity/);
  assert.notEqual(JSON.parse(await readFile(marker, 'utf8')).sessionId, 'session-unexpected');
});

for (const contents of ['{broken', 'null', '{}', '{"version":2}']) {
  test(`refuses corrupt mapping ${contents}`, async (t) => {
    const { options, marker } = await fixture(t);
    await writeFile(marker, contents, { mode: 0o600 });
    const { ctx, calls } = mock(options);
    await assert.rejects(ensureMainSession(ctx, options), /corrupt/);
    assert.equal(await readFile(marker, 'utf8'), contents);
    assert.deepEqual(calls, []);
  });
}

for (const mismatch of [{ profile: 'eva-other' }, { workspace: '/different' }, { sessionId: '../escape' }, { extra: true }]) {
  test(`refuses mismatched mapping ${JSON.stringify(mismatch)}`, async (t) => {
    const { options, marker } = await fixture(t);
    await seed(marker, options, mismatch);
    const { ctx, calls } = mock(options);
    await assert.rejects(ensureMainSession(ctx, options), /corrupt or mismatched/);
    assert.deepEqual(calls, []);
  });
}

test('refuses mapping symlinks, including dangling ones, and directory mappings', async (t) => {
  const { options, marker, base } = await fixture(t);
  const { ctx, calls } = mock(options);
  await symlink(join(base, 'missing'), marker);
  await assert.rejects(ensureMainSession(ctx, options), /unsafe/);
  assert.ok((await lstat(marker)).isSymbolicLink());
  assert.equal(resolve(marker), join(options.stateDir, 'main-session.json'));
  await unlink(marker);
  await mkdir(marker);
  await assert.rejects(ensureMainSession(ctx, options), /unsafe/);
  assert.deepEqual(calls, []);
});

test('refuses symlink state directory and workspace and nonprivate state', async (t) => {
  const { options, base } = await fixture(t);
  const { ctx, calls } = mock(options);
  const redirected = join(base, 'redirected');
  await symlink(options.stateDir, redirected);
  await assert.rejects(ensureMainSession(ctx, { ...options, stateDir: redirected }), /symlink/);
  await assert.rejects(ensureMainSession(ctx, { ...options, workspace: redirected }), /symlink/);
  await chmod(options.stateDir, 0o755);
  await assert.rejects(ensureMainSession(ctx, options), /private/);
  assert.deepEqual(calls, []);
});

for (const meta of [{ cwd: '/wrong' }, { id: 'session-other' }, { parentSession: 'session-parent' }, { origin: 'subagent' }, { isSeeded: true }]) {
  test(`rejects inspection mismatch ${JSON.stringify(meta)}`, async (t) => {
    const { options, marker } = await fixture(t);
    const mapping = await seed(marker, options);
    const { ctx, calls } = mock(options, new Map([[mapping.sessionId,
      { meta: { id: mapping.sessionId, cwd: options.workspace, isSeeded: false, ...meta }, events: [] }]]));
    await assert.rejects(ensureMainSession(ctx, options), /inspection does not match/);
    assert.deepEqual(calls.map(([name]) => name), ['inspect']);
  });
}

test('requires official persistence barrier and rejects conflicting concurrent options', async (t) => {
  const { options } = await fixture(t);
  const { ctx } = mock(options);
  await assert.rejects(ensureMainSession({ sessionController: ctx.sessionController }, options), /requires injected/);
  const started = ensureMainSession(ctx, options);
  await assert.rejects(ensureMainSession(ctx, { ...options, profile: 'different' }), /Conflicting concurrent/);
  await started;
});

async function upgradeFixture(t, state = 'ready') {
  const { options, marker, base } = await fixture(t);
  const originalWorkspace = join(options.workspace, 'workspace');
  await mkdir(originalWorkspace);
  const mapping = await seed(marker, options, { workspace: originalWorkspace, state });
  const history = [{ type: 'user/message', data: { content: 'preserve this conversation' } },
    { type: 'turn/start', data: { turn: 1 } }];
  const main = { meta: { id: mapping.sessionId, cwd: originalWorkspace, isSeeded: false }, events: structuredClone(history) };
  const other = { meta: { id: 'session-other', cwd: '/elsewhere', isSeeded: false }, events: [{ content: 'untouched' }] };
  const mocked = mock(options, new Map([[mapping.sessionId, main], ['session-other', structuredClone(other)]]));
  return { options, marker, base, originalWorkspace, mapping, history, other, ...mocked };
}

for (const state of ['ready', 'reserved']) {
  test(`upgrades ${state} v1 nested workspace using same Agent and official durable cwd event`, async (t) => {
    const f = await upgradeFixture(t, state);
    const before = await readFile(f.marker, 'utf8');
    const flush = f.ctx.sessionPersistence.flush;
    f.ctx.sessionPersistence.flush = async () => {
      assert.equal(await readFile(f.marker, 'utf8'), before, 'mapping commits only after directory change is durable');
      await flush();
    };
    assert.deepEqual(await ensureMainSession(f.ctx, f.options), { sessionId: f.mapping.sessionId });
    assert.deepEqual(f.calls.map(([name]) => name), ['inspect', 'resolveAgent', 'set', 'flush']);
    assert.deepEqual(f.calls[2], ['set', f.mapping.sessionId, f.options.workspace]);
    const upgraded = JSON.parse(await readFile(f.marker, 'utf8'));
    assert.deepEqual(upgraded, { ...f.mapping, version: 2, state: 'ready', workspace: f.options.workspace,
      originalWorkspace: f.originalWorkspace });
    const main = f.stored.get(f.mapping.sessionId);
    assert.equal(main.meta.cwd, f.originalWorkspace, 'immutable header is not rewritten');
    assert.deepEqual(main.events.slice(0, f.history.length), f.history);
    assert.equal(main.effectiveCwd, f.options.workspace);
    assert.equal(f.durable.get(f.mapping.sessionId).effectiveCwd, f.options.workspace);
    assert.deepEqual(f.stored.get('session-other'), f.other);
    assert.equal(f.stored.size, 2, 'upgrade creates no replacement or duplicate Session');
  });
}

test('completed upgrade preserves a later user cwd and does not rewrite mapping on restart', async (t) => {
  const f = await upgradeFixture(t);
  await ensureMainSession(f.ctx, f.options);
  const bytes = await readFile(f.marker, 'utf8');
  f.durable.get(f.mapping.sessionId).effectiveCwd = '/user/subsequent-directory';
  const restarted = mock(f.options, f.durable);
  await ensureMainSession(restarted.ctx, f.options);
  assert.equal(await readFile(f.marker, 'utf8'), bytes);
  assert.deepEqual(restarted.calls.map(([name]) => name), ['inspect', 'flush']);
  assert.equal(restarted.stored.get(f.mapping.sessionId).effectiveCwd, '/user/subsequent-directory');
});

test('v1 already-root mapping stays byte-identical and does not resolve or reset cwd', async (t) => {
  const { options, marker } = await fixture(t);
  const mapping = await seed(marker, options);
  const before = await readFile(marker, 'utf8');
  const { ctx, calls } = mock(options, new Map([[mapping.sessionId, {
    meta: { id: mapping.sessionId, cwd: options.workspace, isSeeded: false }, events: [], effectiveCwd: '/later-user-choice',
  }]]));
  await ensureMainSession(ctx, options);
  assert.equal(await readFile(marker, 'utf8'), before);
  assert.deepEqual(calls.map(([name]) => name), ['inspect', 'flush']);
});

for (const stage of ['set', 'flush']) {
  test(`failed upgrade ${stage} leaves original v1 mapping recoverable with same ID`, async (t) => {
    const f = await upgradeFixture(t);
    const before = await readFile(f.marker, 'utf8');
    const original = stage === 'set' ? f.ctx.workingDirectory.set : f.ctx.sessionPersistence.flush;
    if (stage === 'set') f.ctx.workingDirectory.set = async () => { throw new Error('set interrupted'); };
    else f.ctx.sessionPersistence.flush = async () => { throw new Error('flush interrupted'); };
    await assert.rejects(ensureMainSession(f.ctx, f.options), /interrupted/);
    assert.equal(await readFile(f.marker, 'utf8'), before);
    assert.equal(f.durable.get(f.mapping.sessionId).effectiveCwd, undefined);
    if (stage === 'set') f.ctx.workingDirectory.set = original;
    else f.ctx.sessionPersistence.flush = original;
    await ensureMainSession(f.ctx, f.options);
    const main = f.stored.get(f.mapping.sessionId);
    assert.equal(JSON.parse(await readFile(f.marker, 'utf8')).sessionId, f.mapping.sessionId);
    assert.equal(main.events.length, f.history.length + 1, 'idempotent retry does not append duplicate cwd changes');
    assert.ok(!f.calls.some(([name]) => name === 'create'));
    assert.deepEqual(f.stored.get('session-other'), f.other);
  });
}

for (const missing of ['resolveAgent', 'workingDirectory', 'set']) {
  test(`nested upgrade requires official ${missing} capability before mutation`, async (t) => {
    const f = await upgradeFixture(t);
    const before = await readFile(f.marker, 'utf8');
    if (missing === 'resolveAgent') delete f.ctx.sessionController.resolveAgent;
    else if (missing === 'workingDirectory') delete f.ctx.workingDirectory;
    else delete f.ctx.workingDirectory.set;
    await assert.rejects(ensureMainSession(f.ctx, f.options), /upgrade requires injected/);
    assert.equal(await readFile(f.marker, 'utf8'), before);
    assert.deepEqual(f.calls.map(([name]) => name), ['inspect']);
    assert.deepEqual(f.stored.get(f.mapping.sessionId).events, f.history);
  });
}

for (const result of [{ error: new Error('writer held') }, { agent: { id: 'session-wrong' } }, {}]) {
  test(`upgrade refuses failed or mismatched Agent resolution ${Object.keys(result).join(',')}`, async (t) => {
    const f = await upgradeFixture(t);
    const before = await readFile(f.marker, 'utf8');
    f.ctx.sessionController.resolveAgent = async () => result;
    await assert.rejects(ensureMainSession(f.ctx, f.options), /could not resolve its exact Agent/);
    assert.equal(await readFile(f.marker, 'utf8'), before);
    assert.deepEqual(f.calls.map(([name]) => name), ['inspect']);
  });
}

test('upgrade refuses a nested path symlink and a mismatched immutable original header', async (t) => {
  const f = await upgradeFixture(t);
  const before = await readFile(f.marker, 'utf8');
  f.stored.get(f.mapping.sessionId).meta.cwd = f.options.workspace;
  await assert.rejects(ensureMainSession(f.ctx, f.options), /inspection does not match/);
  f.stored.get(f.mapping.sessionId).meta.cwd = f.originalWorkspace;
  assert.equal(resolve(f.originalWorkspace), join(f.options.workspace, 'workspace'));
  await rm(f.originalWorkspace, { recursive: true });
  await symlink(f.base, f.originalWorkspace);
  await assert.rejects(ensureMainSession(f.ctx, f.options), /symlink/);
  assert.equal(await readFile(f.marker, 'utf8'), before);
  assert.ok(!f.calls.some(([name]) => name === 'create' || name === 'set'));
});

test('fresh Host main Session starts at the supplied home root, not its workspace child', async (t) => {
  const { options: original, marker, base } = await fixture(t);
  const options = { ...original, workspace: base };
  const { ctx, calls } = mock(options);
  const result = await ensureMainSession(ctx, options);
  assert.deepEqual(calls[0], ['create', { cwd: base, sessionId: result.sessionId }]);
  const mapping = JSON.parse(await readFile(marker, 'utf8'));
  assert.equal(mapping.version, 2);
  assert.equal(mapping.workspace, base);
  assert.equal(mapping.originalWorkspace, base);
});

test('restart after durable cwd change but before v2 publish retries without another history event', async (t) => {
  const f = await upgradeFixture(t);
  await f.ctx.workingDirectory.set({ id: f.mapping.sessionId }, f.options.workspace);
  await f.ctx.sessionPersistence.flush();
  assert.equal(JSON.parse(await readFile(f.marker, 'utf8')).version, 1);
  const restarted = mock(f.options, f.durable);
  await ensureMainSession(restarted.ctx, f.options);
  assert.equal(restarted.stored.get(f.mapping.sessionId).events.length, f.history.length + 1);
  assert.deepEqual(restarted.calls.map(([name]) => name), ['inspect', 'resolveAgent', 'set', 'flush']);
  assert.equal(JSON.parse(await readFile(f.marker, 'utf8')).version, 2);
});

for (const fields of [
  { originalWorkspace: '/different' }, { originalWorkspace: '/tmp/../different' },
  { workspace: '/different' }, { originalWorkspace: undefined }, { extra: true },
]) {
  test(`v2 rejects corrupt or arbitrary original workspace ${JSON.stringify(fields)}`, async (t) => {
    const { options, marker } = await fixture(t);
    await seed(marker, options, { version: 2, originalWorkspace: options.workspace, ...fields });
    const { ctx, calls } = mock(options);
    await assert.rejects(ensureMainSession(ctx, options), /corrupt or mismatched/);
    assert.deepEqual(calls, []);
  });
}
