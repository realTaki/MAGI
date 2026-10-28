// Test-only Host fixture. Never edit Session storage or publish credentials.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile, realpath, open } from 'node:fs/promises';
import { join } from 'node:path';
import { ensureMainSession } from '../main-session.js';

export const name = 'eva-upgrade-test-child';
export const inject = ['appReady', 'appExit', 'sessionController', 'sessionPersistence',
  'workingDirectory', 'workspaceRegistry', 'profileContext'];

function snapshot(inspection) {
  // Root creation may omit depth; native restore materializes its default 0.
  // Normalize ONLY this known equivalent form; retain every other header field
  // and every event so full strict main/other comparisons still detect changes.
  const meta = { ...inspection.meta, delegationDepth:
    inspection.meta.delegationDepth === undefined ? 0 : inspection.meta.delegationDepth };
  return JSON.parse(JSON.stringify({ meta, events: inspection.events }));
}

async function resolve(ctx, id) {
  const result = await ctx.sessionController.resolveAgent(id);
  assert.ok(result.agent && !result.error && result.agent.id === id, 'native Session Agent resolution succeeds');
  return result.agent;
}

async function run(ctx, config, progress) {
  progress('validate-home');
  const home = await realpath(ctx.profileContext.home);
  assert.equal(home, config.home);
  assert.equal(await realpath(process.cwd()), home);
  assert.equal(ctx.profileContext.name, 'eva-legacy');
  const stateDir = join(home, 'eva');
  const nested = join(home, 'workspace');
  const mappingPath = join(stateDir, 'main-session.json');
  const preservePath = join(nested, 'preserve.txt');
  if (config.phase === 'seed') {
    progress('seed-directories');
    await mkdir(nested, { mode: 0o700 });
    await mkdir(stateDir, { mode: 0o700 });
    const legacyProject = await ctx.workspaceRegistry.create(nested, 'EVA legacy upgrade test');
    const mainId = `session-${randomUUID()}`;
    const otherId = `session-${randomUUID()}`;
    progress('seed-native-sessions');
    const main = await ctx.sessionController.create({ cwd: nested, sessionId: mainId });
    const other = await ctx.sessionController.create({ cwd: nested, sessionId: otherId });
    assert.equal(main.sessionId, mainId);
    assert.equal(other.sessionId, otherId);
    progress('seed-native-history');
    await ctx.sessionController.rename({ sessionId: mainId, title: 'Preserved legacy main' });
    await ctx.sessionController.rename({ sessionId: otherId, title: 'Preserved other Session' });
    await ctx.sessionPersistence.flush();
    assert.equal(await ctx.workingDirectory.ensure(await resolve(ctx, mainId)), nested);
    const mainBefore = snapshot(await ctx.sessionController.inspect(mainId));
    const otherBefore = snapshot(await ctx.sessionController.inspect(otherId));
    assert.equal(mainBefore.meta.cwd, nested);
    const preserve = 'EVA upgrade test: preserve user workspace bytes.\n';
    await writeFile(preservePath, preserve, { flag: 'wx', mode: 0o600 });
    progress('seed-test-mapping');
    // This is a TEST-OWNED legacy EVA mapping, not native Session storage.
    const mapping = { version: 1, state: 'ready', profile: 'eva-legacy', workspace: nested, sessionId: mainId };
    const handle = await open(mappingPath, 'wx', 0o600);
    try { await handle.writeFile(`${JSON.stringify(mapping)}\n`); await handle.sync(); }
    finally { await handle.close(); }
    await writeFile(config.proofPath, JSON.stringify({ mainId, otherId, mainBefore, otherBefore,
      legacyWorkspaceId: legacyProject.id, preserve }), { flag: 'wx', mode: 0o600 });
    return { phase: 'seed', mainSessionId: mainId, otherSessionId: otherId,
      nativeSessionsSeeded: true, immutableOriginalNested: true };
  }
  assert.equal(config.phase, 'upgrade');
  progress('read-upgrade-mapping');
  const proof = JSON.parse(await readFile(config.proofPath, 'utf8'));
  const beforeBytes = await readFile(mappingPath, 'utf8');
  const before = JSON.parse(beforeBytes);
  progress('register-root');
  const rootProject = await ctx.workspaceRegistry.create(home, 'EVA root upgrade test');
  progress('register-legacy');
  const legacyProject = await ctx.workspaceRegistry.create(nested, 'EVA legacy upgrade test');
  progress('assert-registry');
  assert.equal(legacyProject.id, proof.legacyWorkspaceId);
  assert.notEqual(rootProject.id, legacyProject.id);
  progress('ensure-main-session');
  const result = await ensureMainSession(ctx, { workspace: home, stateDir, profile: 'eva-legacy' });
  progress('assert-main-identity');
  assert.equal(result.sessionId, proof.mainId);
  progress('resolve-main-agent');
  const agent = await resolve(ctx, result.sessionId);
  progress('ensure-effective-root');
  const effective = await ctx.workingDirectory.ensure(agent);
  progress('assert-effective-root');
  assert.equal(effective, home);
  progress('inspect-main');
  const main = snapshot(await ctx.sessionController.inspect(result.sessionId));
  progress('assert-main-header');
  assert.equal(main.meta.cwd, nested, 'immutable native header remains unchanged');
  assert.deepEqual(main.meta, proof.mainBefore.meta);
  progress('assert-main-history');
  assert.deepEqual(main.events.slice(0, proof.mainBefore.events.length), proof.mainBefore.events);
  progress('inspect-other');
  const other = snapshot(await ctx.sessionController.inspect(proof.otherId));
  progress('assert-other-history');
  assert.deepEqual(other, proof.otherBefore);
  progress('assert-preserved-file');
  assert.equal(await readFile(preservePath, 'utf8'), proof.preserve);
  progress('assert-v2-mapping');
  const afterBytes = await readFile(mappingPath, 'utf8');
  const mapping = JSON.parse(afterBytes);
  assert.deepEqual(mapping, { version: 2, state: 'ready', profile: 'eva-legacy', workspace: home,
    sessionId: proof.mainId, originalWorkspace: nested });
  if (before.version === 2) assert.equal(afterBytes, beforeBytes, 'completed-upgrade restart preserves marker bytes');
  else assert.equal(before.version, 1);
  progress('flush-upgrade-proof');
  await ctx.sessionPersistence.flush();
  return { phase: 'upgrade', mainSessionId: proof.mainId, otherSessionId: proof.otherId,
    effectiveDirectoryIsRoot: true, immutableOriginalNested: true, originalHistoryPreserved: true,
    otherSessionPreserved: true, workspaceFilePreserved: true, additiveRootRegistration: true,
    completedRestart: before.version === 2, mappingUnchangedOnRestart: before.version === 2 };
}

export function apply(ctx, config) {
  const progress = (stage) => {
    if (process.connected) process.send({ type: 'eva-upgrade-progress', phase: config.phase, stage });
  };
  progress('mounted');
  ctx.effect(() => ctx.appReady.onReady(() => {
    progress('app-ready');
    void run(ctx, config, progress).then(async (result) => {
      progress('native-checks-flushed');
      if (process.connected) {
        await new Promise((done, fail) => process.send({ type: 'eva-upgrade-proof', ...result },
          (error) => error ? fail(error) : done()));
      }
      await ctx.appExit(0);
    }).catch((error) => {
      // Never transmit messages, stack text, actual/expected values, or log output.
      const names = ['Error', 'AssertionError', 'TypeError', 'RangeError', 'SyntaxError', 'RemoteError'];
      const operators = ['strictEqual', 'deepStrictEqual', 'notStrictEqual', 'notDeepStrictEqual', '==', '!=', 'fail'];
      const match = typeof error?.stack === 'string' && /upgrade-child\.js:(\d+):/.exec(error.stack);
      if (process.connected) process.send({ type: 'eva-upgrade-failed',
        errorName: names.includes(error?.name) ? error.name : 'Other',
        errorCode: error?.code === 'ERR_ASSERTION' ? 'ERR_ASSERTION' : 'OTHER',
        operator: operators.includes(error?.operator) ? error.operator : 'OTHER',
        fixtureLine: match ? Number(match[1]) : 0,
        changedMetaKeys: error?.code === 'ERR_ASSERTION' && error.operator === 'deepStrictEqual' &&
          error.actual && error.expected && typeof error.actual === 'object' && typeof error.expected === 'object'
          ? [...new Set([...Object.keys(error.actual), ...Object.keys(error.expected)])]
            .filter((key) => /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key) &&
              JSON.stringify(error.actual[key]) !== JSON.stringify(error.expected[key])).slice(0, 32) : [] });
      void ctx.appExit(1);
    });
  }));
}
