// Optional real-native prompt-registry probe, outside the *.test.js suite.
// ELECTRON_RUN_AS_NODE=1 /Applications/MAGI.app/Contents/MacOS/MAGI --expose-internals \
//   packages/bot-mode/test/coordinator-native.js /absolute/path/to/dsh/package.json
// No Host, profile, actual Agent, model request, or file write is created.
// Synthetic agent identities exercise prompt isolation, NOT subagent scheduling
// or a model/runtime responsiveness benchmark.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { isAbsolute } from 'node:path';
import { MAIN_COORDINATOR_POLICY, registerMainCoordinator } from '../coordinator.js';

const installAnchor = process.argv[2];
assert.equal(process.argv.length, 3, 'Pass exactly one native DSH installation package.json anchor');
assert.ok(isAbsolute(installAnchor), 'Installation anchor must be absolute');
assert.equal(process.env.ELECTRON_RUN_AS_NODE, '1', 'Run with MAGI Electron in Node mode');
assert.ok(process.versions.electron, 'This ASAR-aware probe requires MAGI Electron');
const native = createRequire(installAnchor);
const { Context } = native('@deepseek-ai/cordis');
const { default: SystemPrompt, renderPrompt } = native('@deepseek-ai/dsh-system-prompt');
const { assembleContextFor } = native('@deepseek-ai/dsh-agent');

assert.equal(typeof MAIN_COORDINATOR_POLICY, 'string');
assert.ok(MAIN_COORDINATOR_POLICY.trim().length > 0);
const ctx = new Context();
const main = Object.freeze({ id: 'coordinator-native-main' });
const unrelated = Object.freeze({ id: 'coordinator-native-other' });
// These are identity-only fixtures, not real spawned/forked Agents.
const spawnedChild = Object.freeze({ id: 'coordinator-native-spawn-child' });
const forkedChild = Object.freeze({ id: 'coordinator-native-fork-child' });
let identity;
const current = () => identity;
const readPrompt = async agent => renderPrompt(await ctx.systemPrompt.assemble(
  agent === undefined ? {} : assembleContextFor(agent),
));

try {
  await ctx.plugin(SystemPrompt, { includeHarnessIdentity: false });
  let disposeRegistration;
  function coordinatorProbe(ownerCtx) {
    disposeRegistration = registerMainCoordinator(ownerCtx, current);
  }
  coordinatorProbe.inject = ['systemPrompt'];
  const owner = ctx.plugin(coordinatorProbe);
  await owner;
  assert.equal(typeof disposeRegistration, 'function', 'Registration exposes a native effect disposer');
  assert.equal(await readPrompt(main), '', 'No identity published: no coordinator instructions');
  assert.equal(await readPrompt(), '', 'Unaddressed assembly never receives coordinator instructions');

  identity = Object.freeze({ sessionId: main.id, workspace: '/synthetic/coordinator-native' });
  assert.equal(await readPrompt(main), MAIN_COORDINATOR_POLICY, 'Fixed main receives coordinator policy');
  for (const agent of [unrelated, spawnedChild, forkedChild]) {
    assert.equal(await readPrompt(agent), '', 'Different agent identity never receives coordinator policy');
  }
  assert.equal(await readPrompt(), '', 'Published identity does not affect unaddressed assembly');
  assert.equal(await readPrompt({ id: main.id }), MAIN_COORDINATOR_POLICY,
    'Main selection follows stable Session id, not an earlier Agent object');

  identity = undefined;
  assert.equal(await readPrompt(main), '', 'Identity revocation is observed on the next assembly');
  identity = Object.freeze({ sessionId: main.id, workspace: '/synthetic/coordinator-native' });
  assert.equal(await readPrompt(main), MAIN_COORDINATOR_POLICY);
  await disposeRegistration();
  assert.equal(await readPrompt(main), '', 'Explicit effect disposal removes coordinator policy');
  await owner.dispose();

  // Register again under a separate native plugin Context, then unload only
  // that Context while the real SystemPrompt service remains alive.
  function coordinatorReloadProbe(ownerCtx) {
    registerMainCoordinator(ownerCtx, current);
  }
  coordinatorReloadProbe.inject = ['systemPrompt'];
  const reloadOwner = ctx.plugin(coordinatorReloadProbe);
  await reloadOwner;
  assert.equal(await readPrompt(main), MAIN_COORDINATOR_POLICY);
  await reloadOwner.dispose();
  assert.equal(await readPrompt(main), '', 'Owner Context unload removes the registration automatically');
  assert.equal(await readPrompt(unrelated), '');
  console.log('Native coordinator prompt isolation/disposal probe passed (no model or subagent execution).');
} finally {
  await ctx.fiber.dispose();
}
