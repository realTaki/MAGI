import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, writeFile, rm, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { apply, inject } from '../index.js';

test('standalone DM depends on the explicit Host transport service', () => {
  assert.ok(inject.includes('magiDmTransport'));
  assert.equal(new Set(inject).size, inject.length);
});

async function fixture(t) {
  const base = await realpath(tmpdir());
  const root = await mkdtemp(join(base, 'magi-dm-child-test-'));
  t.after(async () => {
    assert.equal(resolve(root), root);
    assert.ok(root.startsWith(join(base, 'magi-dm-child-test-')));
    await rm(root, { recursive: true, force: true });
  });
  const installAnchor = join(root, 'package.json');
  await writeFile(installAnchor, JSON.stringify({ name: 'dm-child-test-anchor', private: true, type: 'module' }), { mode: 0o600 });
  let registrations = 0;
  const ctx = {
    profileContext: { installAnchor, name: 'eva-child', dir: join(root, 'profile'), home: root },
    magiDmTransport: { kind: 'child', handle: 'mismatch', workspace: root, channel: { connected: false, send() {} } },
    provide() { assert.fail('invalid transport must not publish a DM installation'); },
    tools: { register() { registrations++; assert.fail('invalid child must not register a tool'); } },
    effect() { assert.fail('invalid child must not install lifecycle effects'); },
  };
  return { root, ctx, registrations: () => registrations };
}

for (const transport of [undefined, {}, { kind: 'unsupported' }]) {
  test(`unsupported transport ${transport === undefined ? 'missing' : transport.kind ?? 'empty'} rejects before dependency/state/tool access`, async t => {
    const { ctx, registrations } = await fixture(t);
    ctx.magiDmTransport = transport;
    await assert.rejects(apply(ctx), /supported Host-owned transport adapter/);
    assert.equal(registrations(), 0);
    await assert.rejects(readdir(ctx.profileContext.dir), { code: 'ENOENT' });
  });
}

for (const channel of [undefined, null, {}, { connected: true }]) {
  test(`child transport requires its own explicit send channel (${JSON.stringify(channel)})`, async t => {
    const { ctx, registrations } = await fixture(t);
    ctx.magiDmTransport.channel = channel;
    await assert.rejects(apply(ctx), /explicit managed parent IPC channel/);
    assert.equal(registrations(), 0);
    await assert.rejects(readdir(ctx.profileContext.dir), { code: 'ENOENT' });
  });
}

test('broker binds after publishing its service and owns unbinding on plugin disposal', async t => {
  const { root, ctx } = await fixture(t), cleanups = [], services = new Map(), calls = [];
  const tools = join(root, 'node_modules', '@deepseek-ai', 'dsh-tools');
  await mkdir(tools, { recursive: true, mode: 0o700 });
  await writeFile(join(tools, 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh-tools', type: 'module', exports: './index.js' }));
  await writeFile(join(tools, 'index.js'), 'export const defineTool = definition => definition;\n');
  await mkdir(ctx.profileContext.dir, { mode: 0o700 });
  ctx.tools.register = () => () => {};
  ctx.effect = callback => { const off = callback(); if (off) cleanups.push(off); };
  ctx.appReady = { onReady: () => () => {} };
  ctx.provide = (key, value) => { services.set(key, value); calls.push('provide'); };
  ctx.magiDmTransport = { kind: 'broker', workspace: root, peers: async () => [], bindBroker(attach) {
    assert.equal(attach, services.get('magiDm').attach);
    calls.push('bind'); return () => calls.push('unbind');
  } };
  try {
    await apply(ctx);
    await services.get('magiDm').host;
    assert.deepEqual(calls, ['provide', 'bind']);
  } finally { for (const off of cleanups.reverse()) await off(); }
  assert.deepEqual(calls, ['provide', 'bind', 'unbind']);
});

test('absolute child entry resolves DSH tools from the profile install anchor before checking managed IPC', async t => {
  const { root, ctx, registrations } = await fixture(t);
  const tools = join(root, 'node_modules', '@deepseek-ai', 'dsh-tools');
  await mkdir(tools, { recursive: true, mode: 0o700 });
  await writeFile(join(tools, 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh-tools', type: 'module', exports: './index.js' }), { mode: 0o600 });
  await writeFile(join(tools, 'index.js'), 'export const defineTool = definition => definition;\n', { mode: 0o600 });
  await assert.rejects(apply(ctx), error => {
    assert.notEqual(error.code, 'ERR_MODULE_NOT_FOUND', 'actual child module must import tools from the synthetic anchor, not its source directory');
    assert.match(error.message, /DM child requires its managed parent IPC channel and exact profile/);
    return true;
  });
  // Connected IPC alone must not admit a mismatched fixed child identity.
  ctx.magiDmTransport.channel = { connected: true, send() { assert.fail('mismatched identity must not send'); } };
  await assert.rejects(apply(ctx), /managed parent IPC channel and exact profile/);
  assert.equal(registrations(), 0);
  await assert.rejects(readdir(ctx.profileContext.dir), { code: 'ENOENT' });
});

test('missing anchored DSH tools rejects before registering tools or opening profile state', async t => {
  const { ctx, registrations } = await fixture(t);
  await assert.rejects(apply(ctx), error => {
    assert.equal(error.code, 'MODULE_NOT_FOUND', 'createRequire.resolve reports a missing anchored dependency before ESM import');
    assert.match(error.message, /@deepseek-ai\/dsh-tools/);
    assert.doesNotMatch(error.message, /managed parent IPC/);
    return true;
  });
  assert.equal(registrations(), 0);
  await assert.rejects(readdir(ctx.profileContext.dir), { code: 'ENOENT' });
});
