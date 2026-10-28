// Real native-service upgrade proof. Run ONLY with an isolated temporary HOME.
// Arguments: the installed DSH resolution anchor and a dedicated temporary base.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { access, mkdir, realpath, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { desktopRuntime } from '../runtime.js';

const [anchor, providedBase] = process.argv.slice(2);
assert.ok(anchor && providedBase, 'provide installed DSH anchor and dedicated temporary base');
assert.ok(isAbsolute(providedBase), 'temporary base must be absolute');
const base = await realpath(providedBase);
const homeRoot = await realpath(homedir());
assert.equal(homeRoot, resolve(process.env.HOME), 'HOME must be explicitly set to an isolated canonical directory');
assert.notEqual(homeRoot, '/');
assert.notEqual(base, '/');
const home = join(homeRoot, '.dsh-eva-legacy');
// Refuse an existing node rather than modifying any real user's profile/data.
await assert.rejects(access(home), { code: 'ENOENT' });
await mkdir(home, { mode: 0o700 });
const here = dirname(fileURLToPath(import.meta.url));
const runner = join(here, '../runner.js');
const fixture = join(here, 'upgrade-child.js');
const proofPath = join(base, 'legacy-upgrade-proof.json');
const runtime = desktopRuntime(anchor);
const live = new Set();

function killTree(child) {
  if (!child.pid) return;
  try {
    if (process.platform !== 'win32') process.kill(-child.pid, 'SIGKILL');
    else child.kill('SIGKILL');
  } catch (error) { if (error.code !== 'ESRCH') throw new Error('Upgrade test child cleanup failed.'); }
}

async function phase(phaseName, index) {
  const patch = join(base, `legacy-upgrade-${index}.patch.yml`);
  await writeFile(patch, `${JSON.stringify([{ insert: [{ id: 'eva-upgrade-test-child', name: fixture,
    config: { phase: phaseName, home, proofPath } }] }], null, 2)}\n`, { flag: 'wx', mode: 0o600 });
  const args = ['--profile', 'eva-legacy'];
  if (phaseName === 'seed') args.push('--from-default-profile', 'web');
  args.push('--patch', patch, '--host', '127.0.0.1', '--port', '0', '--no-open');
  const env = { ...process.env, DSH_HOME: home, DSH_TELEMETRY_DISABLED: '1', ELECTRON_RUN_AS_NODE: '1' };
  delete env.DSH_WEB_URL;
  for (const key of Object.keys(env)) {
    if (/(?:API_?KEY|ACCESS_TOKEN|REFRESH_TOKEN|AUTH_TOKEN)$/i.test(key)) delete env[key];
  }
  const child = spawn(runtime.executable,
    ['--expose-internals', runner, runtime.cli, runtime.runtimeDir, runtime.supportDir, ...args], {
      cwd: home, env, stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
      windowsHide: true, detached: process.platform !== 'win32',
    });
  live.add(child);
  // Drain but never retain/print child logs: Web startup may contain credentials.
  child.stdout.resume();
  child.stderr.resume();
  return new Promise((done, fail) => {
    let proof;
    let invalid = false;
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.removeAllListeners('message');
      killTree(child);
      if (child.connected) child.disconnect();
      child.stdout.destroy();
      child.stderr.destroy();
      live.delete(child);
      if (ok && !invalid && proof) done(proof);
      else fail(new Error(`Native EVA upgrade integration phase failed (${phaseName}/${child.evaLastStage ?? 'starting'}); safe failure=${JSON.stringify(child.evaFailure ?? {})}; child output withheld.`));
    };
    const timer = setTimeout(() => finish(false), 90_000);
    child.on('message', (message) => {
      const stages = ['mounted', 'app-ready', 'native-checks-flushed', 'validate-home',
        'seed-directories', 'seed-native-sessions', 'seed-native-history', 'seed-test-mapping',
        'read-upgrade-mapping', 'register-root', 'register-legacy', 'assert-registry',
        'ensure-main-session', 'assert-main-identity', 'resolve-main-agent', 'ensure-effective-root',
        'assert-effective-root', 'inspect-main', 'assert-main-header', 'assert-main-history',
        'inspect-other', 'assert-other-history', 'assert-preserved-file', 'assert-v2-mapping', 'flush-upgrade-proof'];
      if (message?.type === 'eva-upgrade-progress' && message.phase === phaseName &&
          stages.includes(message.stage)) child.evaLastStage = message.stage;
      if (message?.type === 'eva-upgrade-failed') {
        const names = ['Error', 'AssertionError', 'TypeError', 'RangeError', 'SyntaxError', 'RemoteError'];
        const operators = ['strictEqual', 'deepStrictEqual', 'notStrictEqual', 'notDeepStrictEqual', '==', '!=', 'fail'];
        child.evaFailure = {
          errorName: names.includes(message.errorName) ? message.errorName : 'Other',
          errorCode: message.errorCode === 'ERR_ASSERTION' ? 'ERR_ASSERTION' : 'OTHER',
          operator: operators.includes(message.operator) ? message.operator : 'OTHER',
          fixtureLine: Number.isSafeInteger(message.fixtureLine) && message.fixtureLine > 0 && message.fixtureLine < 10000 ? message.fixtureLine : 0,
          changedMetaKeys: Array.isArray(message.changedMetaKeys) ? message.changedMetaKeys
            .filter((key) => typeof key === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key)).slice(0, 32) : [],
        };
        invalid = true; finish(false); return;
      }
      if (message?.type === 'eva-upgrade-proof' && message.phase === phaseName &&
          typeof message.mainSessionId === 'string' && typeof message.otherSessionId === 'string') {
        // Only the fixture's bounded, credential-free proof protocol is retained.
        proof = { phase: message.phase, mainSessionId: message.mainSessionId,
          otherSessionId: message.otherSessionId, nativeSessionsSeeded: message.nativeSessionsSeeded,
          effectiveDirectoryIsRoot: message.effectiveDirectoryIsRoot,
          immutableOriginalNested: message.immutableOriginalNested,
          originalHistoryPreserved: message.originalHistoryPreserved,
          otherSessionPreserved: message.otherSessionPreserved,
          workspaceFilePreserved: message.workspaceFilePreserved,
          additiveRootRegistration: message.additiveRootRegistration,
          completedRestart: message.completedRestart,
          mappingUnchangedOnRestart: message.mappingUnchangedOnRestart };
        // Proof is emitted only after native persistence.flush completed. The
        // runner's referenced IPC channel can otherwise outlive appExit(0), so
        // the controller owns terminal cleanup instead of waiting on that exit.
        finish(true);
      }
    });
    child.once('error', () => finish(false));
    child.once('exit', (code, signal) => finish(code === 0 && signal === null));
    child.once('close', (code, signal) => finish(code === 0 && signal === null));
  });
}

try {
  const seeded = await phase('seed', 0);
  assert.equal(seeded.nativeSessionsSeeded, true);
  const upgraded = await phase('upgrade', 1);
  const restarted = await phase('upgrade', 2);
  for (const result of [upgraded, restarted]) {
    assert.equal(result.mainSessionId, seeded.mainSessionId);
    assert.equal(result.otherSessionId, seeded.otherSessionId);
    for (const key of ['effectiveDirectoryIsRoot', 'immutableOriginalNested', 'originalHistoryPreserved',
      'otherSessionPreserved', 'workspaceFilePreserved', 'additiveRootRegistration']) assert.equal(result[key], true);
  }
  assert.equal(upgraded.completedRestart, false);
  assert.equal(restarted.completedRestart, true);
  assert.equal(restarted.mappingUnchangedOnRestart, true);
  console.log(JSON.stringify({ result: 'passed', proof: 'real native EVA v1 nested-to-root Session upgrade',
    sameMainSession: true, effectiveDirectoryIsRoot: true, immutableOriginalNested: true,
    originalHistoryPreserved: true, otherSessionPreserved: true, workspaceFilePreserved: true,
    additiveRootRegistration: true, restartReusesMain: true, mappingUnchangedOnRestart: true }));
} finally {
  for (const child of live) {
    killTree(child);
    if (child.connected) child.disconnect();
    child.stdout?.destroy();
    child.stderr?.destroy();
  }
}
