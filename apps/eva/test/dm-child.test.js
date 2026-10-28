import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { apply } from '../dm-child.js';

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
    profileContext: { installAnchor, name: 'eva-child' },
    tools: { register() { registrations++; assert.fail('invalid child must not register a tool'); } },
    effect() { assert.fail('invalid child must not install lifecycle effects'); },
  };
  return { root, ctx, registrations: () => registrations };
}

test('absolute child entry resolves DSH tools from the profile install anchor before checking managed IPC', async t => {
  const { root, ctx, registrations } = await fixture(t);
  const tools = join(root, 'node_modules', '@deepseek-ai', 'dsh-tools');
  await mkdir(tools, { recursive: true, mode: 0o700 });
  await writeFile(join(tools, 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh-tools', type: 'module', exports: './index.js' }), { mode: 0o600 });
  await writeFile(join(tools, 'index.js'), 'export const defineTool = definition => definition;\n', { mode: 0o600 });
  await assert.rejects(apply(ctx, { profile: 'mismatch' }), error => {
    assert.notEqual(error.code, 'ERR_MODULE_NOT_FOUND', 'actual child module must import tools from the synthetic anchor, not its source directory');
    assert.match(error.message, /DM child requires its managed parent IPC channel and exact profile/);
    return true;
  });
  assert.equal(registrations(), 0);
});

test('missing anchored DSH tools rejects before registering tools or opening profile state', async t => {
  const { ctx, registrations } = await fixture(t);
  await assert.rejects(apply(ctx, { profile: 'mismatch' }), error => {
    assert.equal(error.code, 'MODULE_NOT_FOUND', 'createRequire.resolve reports a missing anchored dependency before ESM import');
    assert.match(error.message, /@deepseek-ai\/dsh-tools/);
    assert.doesNotMatch(error.message, /managed parent IPC/);
    return true;
  });
  assert.equal(registrations(), 0);
});
