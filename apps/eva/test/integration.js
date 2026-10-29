// Real bundled-runtime proof using Host-context/approval fixtures, NOT installed registration.
// Run with a dedicated temporary HOME to avoid using or changing real node directories.
import assert from 'node:assert/strict';
import { createRequire, registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve, join } from 'node:path';
import { homedir } from 'node:os';
import { readFile, access } from 'node:fs/promises';

const [anchor, base] = process.argv.slice(2);
assert.ok(anchor && base);
const toolsUrl = pathToFileURL(createRequire(anchor).resolve('@deepseek-ai/dsh-tools')).href;
registerHooks({ resolve(specifier, context, next) {
  if (specifier === '@deepseek-ai/dsh-tools') return next(toolsUrl, context);
  return next(specifier, context);
} });
const { apply } = await import('../index.js');
// Discovery boundary only: child providers below are real native services.
const { default: LocalCredentialProvider } = await import(pathToFileURL(createRequire(anchor).resolve('@deepseek-ai/dsh-credentials-local')).href);
const parentCredentials = Object.assign(Object.create(LocalCredentialProvider.prototype), {
  config: { path: join(base, 'synthetic-parent-credentials.yaml'), watch: true },
});
let tool;
const cleanup = [];
const routes = new Map();
const ctx = {
  profileContext: { installAnchor: anchor, home: homedir() },
  credentials: parentCredentials,
  connection: { fetch: { register(route) { routes.set(route.path, route); return () => routes.delete(route.path); } } },
  tools: { register(definition) { tool = definition; } },
  workingDirectory: { async ensure() { return resolve(base); } },
  approval: { async request() { return 'allowed-once'; } },
  effect(effect) { cleanup.push(effect()); },
  inject(dependencies) { assert.deepEqual(dependencies, ['skills']); /* Optional service absent in this fixture. */ },
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
  assert.ok(proof.start.mainSessionId);
  assert.equal(handle, 'eva-000');
  assert.equal(proof.start.status, 'running');
  assert.ok(proof.start.pid !== process.pid);
  assert.equal(proof.start.home, join(homedir(), '.dsh-eva-000'));
  assert.equal(proof.start.workspace, proof.start.home);
  await assert.rejects(access(join(proof.start.home, 'workspace')));
  assert.equal(proof.start.cwd, proof.start.home);
  assert.equal(proof.start.cwd, proof.start.workspace);
  assert.equal(proof.start.defaultAgentPreset, 'eva');
  await assert.rejects(access(join(homedir(), 'Documents')));
  assert.equal(proof.start.profileDir, join(proof.start.home, 'profiles/eva-000'));
  assert.ok(proof.start.workspaceId);
  const response = await fetch(proof.start.origin, { redirect: 'manual', signal: AbortSignal.timeout(5000) });
  proof.httpStatus = response.status;
  assert.equal(response.status, 401);
  await response.arrayBuffer();
  const childViewURL = new URL('/api/eva/view', proof.start.origin);
  const unauthenticatedView = await fetch(childViewURL, { redirect: 'manual', signal: AbortSignal.timeout(5000) });
  assert.equal(unauthenticatedView.status, 401);
  await unauthenticatedView.arrayBuffer();

  // This direct handler invocation represents an authenticated parent owner click;
  // actual child HTTP authentication is exercised below, not mocked.
  const openRoute = routes.get('/api/eva/open');
  assert.ok(openRoute && openRoute.methods.includes('GET'));
  const openResponse = await openRoute.fetch(new Request(`http://127.0.0.1/api/eva/open?handle=${encodeURIComponent(handle)}`));
  assert.equal(openResponse.status, 303);
  assert.equal(openResponse.headers.get('cache-control'), 'no-store');
  assert.equal(openResponse.headers.get('referrer-policy'), 'no-referrer');
  const privateLocation = openResponse.headers.get('location');
  assert.ok(privateLocation, 'explicit owner navigation returns a private launch location');
  const launch = new URL(privateLocation);
  assert.ok(launch.origin === proof.start.origin, 'launch stays on the child origin');
  assert.ok(launch.hash === '#eva-main', 'launch selects the child main-conversation view');
  // Never print or serialize the credential-bearing location or cookies.
  const login = await fetch(launch, { redirect: 'manual', signal: AbortSignal.timeout(5000) });
  assert.equal(login.status, 303);
  const setCookies = login.headers.getSetCookie();
  assert.ok(setCookies.length > 0, 'child login creates an authentication cookie');
  const cookie = setCookies.map((value) => value.split(';', 1)[0]).join('; ');
  const shellLocation = login.headers.get('location');
  assert.ok(shellLocation, 'child login redirects to the authenticated shell');
  const shellURL = new URL(shellLocation, proof.start.origin);
  assert.ok(shellURL.origin === proof.start.origin, 'authenticated shell stays on the child origin');
  await login.arrayBuffer();
  const childView = await fetch(childViewURL, { headers: { Cookie: cookie }, redirect: 'manual', signal: AbortSignal.timeout(5000) });
  assert.equal(childView.status, 200);
  assert.deepEqual(await childView.json(), { mode: 'child', mainSessionId: proof.start.mainSessionId });
  const shell = await fetch(shellURL, { headers: { Cookie: cookie }, redirect: 'manual', signal: AbortSignal.timeout(5000) });
  assert.equal(shell.status, 200);
  const shellText = await shell.text();
  assert.ok(shellText.includes('@local/eva'), 'authenticated child shell includes the EVA Client plugin graph');
  proof.authenticatedChildView = true;
  proof.authenticatedChildClientGraph = true;
  proof.ownerClickRedirect = true;

  proof.profile = JSON.parse(await readFile(join(proof.start.profileDir, 'package.json'), 'utf8'));
  assert.ok(proof.profile.dsh.profile.bundles.length > 0);
  assert.equal((await tool.execute({ action: 'start', handle }, exec)).pid, proof.start.pid);
  proof.status = await tool.execute({ action: 'status', handle }, exec);
  assert.equal(proof.status.mainSessionId, proof.start.mainSessionId);
  proof.automaticSecond = await tool.execute({ action: 'start' }, exec);
  assert.equal(proof.automaticSecond.handle, 'eva-001');
  assert.equal((await tool.execute({ action: 'status' }, exec)).nodes.length, 2);
  await tool.execute({ action: 'stop', handle: proof.automaticSecond.handle }, exec);
  const mappingPath = join(proof.start.home, 'eva/main-session.json');
  const mappingBeforeRestart = await readFile(mappingPath, 'utf8');
  assert.equal(JSON.parse(mappingBeforeRestart).sessionId, proof.start.mainSessionId);
  proof.stop = await tool.execute({ action: 'stop', handle }, exec);
  assert.equal(proof.stop.status, 'stopped');
  await assert.rejects(access(join(proof.start.home, 'eva/owner.lock')));
  const restarted = await tool.execute({ action: 'start', handle }, exec);
  assert.notEqual(restarted.pid, proof.start.pid);
  assert.equal(restarted.workspace, proof.start.home);
  assert.equal(restarted.workspaceId, proof.start.workspaceId);
  assert.equal(restarted.cwd, restarted.home);
  assert.equal(restarted.defaultAgentPreset, 'eva');
  assert.equal(restarted.mainSessionId, proof.start.mainSessionId);
  assert.ok(await readFile(mappingPath, 'utf8') === mappingBeforeRestart, 'restart leaves main Session mapping bytes unchanged');
  proof.persistentMainSession = true;
  proof.mainMappingUnchanged = true;
  proof.restartPid = restarted.pid;
} finally {
  for (const dispose of cleanup.reverse()) await dispose();
}
proof.afterUnload = await tool.execute({ action: 'status', handle: proof.start.handle }, exec);
assert.equal(proof.afterUnload.status, 'stopped');
// Only safe proof facts leave this process; no repository result artifact is written.
console.log(JSON.stringify({ result: 'passed', handle: proof.start.handle, profile: proof.start.profile,
  httpStatus: proof.httpStatus, automaticSecond: proof.automaticSecond.handle, afterUnload: proof.afterUnload.status,
  ownerClickRedirect: proof.ownerClickRedirect, authenticatedChildView: proof.authenticatedChildView,
  authenticatedChildClientGraph: proof.authenticatedChildClientGraph,
  persistentMainSession: proof.persistentMainSession, mainMappingUnchanged: proof.mainMappingUnchanged }));
