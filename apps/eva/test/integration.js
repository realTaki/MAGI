// Real bundled-runtime proof using Host-context/approval fixtures, NOT installed registration.
// Run with a dedicated temporary HOME to avoid using or changing real node directories.
import assert from 'node:assert/strict';
import { createRequire, registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve, join } from 'node:path';
import { homedir } from 'node:os';
import { readFile, writeFile, access, mkdir, rename } from 'node:fs/promises';

const [anchor, base] = process.argv.slice(2);
assert.ok(anchor && base);
const toolsUrl = pathToFileURL(createRequire(anchor).resolve('@deepseek-ai/dsh-tools')).href;
registerHooks({ resolve(specifier, context, next) {
  if (specifier === '@deepseek-ai/dsh-tools') return next(toolsUrl, context);
  return next(specifier, context);
} });
const { apply } = await import('../index.js');
let tool;
const cleanup = [];
const ctx = {
  profileContext: { installAnchor: anchor },
  tools: { register(definition) { tool = definition; } },
  workingDirectory: { async ensure() { return resolve(base); } },
  approval: { async request() { return 'allowed-once'; } },
  effect(effect) { cleanup.push(effect()); },
};
apply(ctx);
assert.equal(tool.name, 'eva');
const exec = { agent: {}, callId: 'eva-integration', signal: new AbortController().signal };
const proof = { testedAt: new Date().toISOString(), integration: 'real eva tool definition; Host context and approval fixture, not installed desktop registration' };
try {
  await assert.rejects(tool.execute({ action: 'start', handle: 'a', name: 'b' }, exec), /Conflicting/);
  await assert.rejects(tool.execute({ action: 'stop' }, exec), /requires a handle/);
  const approve = ctx.approval.request;
  ctx.approval.request = async () => 'rejected';
  await assert.rejects(tool.execute({ action: 'start' }, exec), /not approved/);
  assert.deepEqual((await tool.execute({ action: 'status' }, exec)).nodes, []);
  await assert.rejects(access(join(homedir(), '.dsh-eva-000')));
  proof.approvalDeniedWithoutAllocating = true;
  ctx.approval.request = approve;
  proof.start = await tool.execute({ action: 'start' }, exec);
  const handle = proof.start.handle;
  console.log(JSON.stringify({ stage: 'running', ...proof.start }));
  assert.equal(handle, 'eva-000');
  assert.equal(proof.start.status, 'running');
  assert.ok(proof.start.pid !== process.pid);
  assert.equal(proof.start.home, join(homedir(), '.dsh-eva-000'));
  assert.equal(proof.start.workspace, join(proof.start.home, 'workspace'));
  assert.equal(proof.start.cwd, proof.start.home);
  assert.notEqual(proof.start.cwd, proof.start.workspace);
  assert.equal(proof.start.defaultAgentPreset, 'cordis');
  await assert.rejects(access(join(homedir(), 'Documents')));
  assert.equal(proof.start.profileDir, join(proof.start.home, 'profiles/eva-000'));
  assert.ok(proof.start.workspaceId);
  const response = await fetch(proof.start.origin, { signal: AbortSignal.timeout(5000) });
  proof.httpStatus = response.status;
  assert.equal(response.status, 401);
  await response.arrayBuffer();
  proof.profile = JSON.parse(await readFile(join(proof.start.profileDir, 'package.json'), 'utf8'));
  assert.ok(proof.profile.dsh.profile.bundles.length > 0);
  assert.equal((await tool.execute({ action: 'start', handle }, exec)).pid, proof.start.pid);
  proof.status = await tool.execute({ action: 'status', handle }, exec);
  proof.automaticSecond = await tool.execute({ action: 'start' }, exec);
  assert.equal(proof.automaticSecond.handle, 'eva-001');
  assert.equal((await tool.execute({ action: 'status' }, exec)).nodes.length, 2);
  await tool.execute({ action: 'stop', handle: proof.automaticSecond.handle }, exec);
  proof.stop = await tool.execute({ action: 'stop', handle }, exec);
  assert.equal(proof.stop.status, 'stopped');
  await assert.rejects(access(join(proof.start.home, 'eva/owner.lock')));
  // Simulate a stopped 0.2 node in this temporary HOME, then migrate via start.
  const legacyWorkspace = join(homedir(), 'Documents/deepseek-harness', `${handle}-workspace`);
  await mkdir(resolve(legacyWorkspace, '..'), { recursive: true });
  await rename(proof.start.workspace, legacyWorkspace);
  const registryPath = join(proof.start.home, 'storages/workspace.json');
  const registry = JSON.parse(await readFile(registryPath, 'utf8'));
  registry.tables.workspaces[proof.start.workspaceId].path = legacyWorkspace;
  await writeFile(registryPath, JSON.stringify(registry, null, 2) + '\n');
  const restarted = await tool.execute({ action: 'start', handle }, exec);
  assert.notEqual(restarted.pid, proof.start.pid);
  assert.equal(restarted.workspace, join(proof.start.home, 'workspace'));
  assert.equal(restarted.workspaceId, proof.start.workspaceId);
  assert.equal(restarted.cwd, restarted.home);
  assert.equal(restarted.defaultAgentPreset, 'cordis');
  await assert.rejects(access(legacyWorkspace), { code: 'ENOENT' });
  await assert.rejects(access(join(proof.start.home, 'eva/workspace-relocation.pending.json')), { code: 'ENOENT' });
  proof.registeredLegacyRelocation = { workspaceIdPreserved: true, originalRemoved: true };
  proof.restartPid = restarted.pid;
} finally {
  for (const dispose of cleanup.reverse()) await dispose();
}
proof.afterUnload = await tool.execute({ action: 'status', handle: proof.start.handle }, exec);
assert.equal(proof.afterUnload.status, 'stopped');
await writeFile(new URL('./integration-result.json', import.meta.url), JSON.stringify(proof, null, 2) + '\n');
console.log(JSON.stringify({ result: 'passed', handle: proof.start.handle, profile: proof.start.profile, httpStatus: proof.httpStatus, automaticSecond: proof.automaticSecond.handle, afterUnload: proof.afterUnload.status }));
