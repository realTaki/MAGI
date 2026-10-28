import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
import * as entry from '../child-entry.js';
import * as lifecycle from '../child.js';
import * as dm from '../dm-child.js';

async function isolated(lifecycleApply, dmApply) {
  const source = await readFile(new URL('../child-entry.js', import.meta.url), 'utf8');
  assert.match(source, /^import \* as lifecycle from '\.\/child\.js';$/m);
  assert.match(source, /^import \* as dm from '\.\/dm-child\.js';$/m);
  // Run the actual composite function, substituting only its module boundaries.
  // No readiness callback, native Host, filesystem state or IPC channel starts.
  return runInNewContext(`${source.replace(/^import .*;$/gm, '').replace(/^export /gm, '')}\napply;`, {
    lifecycle: { inject: lifecycle.inject, apply: lifecycleApply },
    dm: { inject: dm.inject, apply: dmApply },
  });
}

test('single child entry advertises the exact duplicate-free lifecycle/DM dependency union', () => {
  assert.equal(entry.name, 'eva-child-host');
  assert.deepEqual(entry.inject, [...new Set([...lifecycle.inject, ...dm.inject])]);
  assert.equal(new Set(entry.inject).size, entry.inject.length);
  assert.equal(entry.inject.filter(key => key === 'profileContext').length, 1);
  assert.equal(entry.inject.filter(key => key === 'appReady').length, 1);
  assert.ok(entry.inject.includes('tools'));
  assert.ok(entry.inject.includes('webServer'));
});

test('composite applies lifecycle once, then awaits DM once in the identical Host scope and profile', async () => {
  const calls = [], ctx = {};
  const config = Object.freeze({ workspace: '/synthetic/eva-home', profile: 'eva-000',
    credentialsPath: '/synthetic/credentials.yaml', credentialSharingVersion: 1 });
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const apply = await isolated((actualCtx, actualConfig) => {
    calls.push('lifecycle');
    assert.equal(actualCtx, ctx);
    assert.equal(actualConfig, config, 'lifecycle retains workspace and sharing configuration');
  }, async (actualCtx, actualConfig) => {
    calls.push('dm');
    assert.equal(actualCtx, ctx, 'DM must not get a sibling or isolated context');
    assert.deepEqual(Object.keys(actualConfig), ['profile']);
    assert.equal(actualConfig.profile, config.profile);
    await gate;
    calls.push('dm-ready');
  });
  let completed = false;
  const applied = apply(ctx, config).then(() => { completed = true; });
  assert.deepEqual(calls, ['lifecycle', 'dm']);
  assert.equal(completed, false, 'entry activation waits for DM initialization');
  release();
  await applied;
  assert.equal(completed, true);
  assert.deepEqual(calls, ['lifecycle', 'dm', 'dm-ready']);
  assert.deepEqual(config, { workspace: '/synthetic/eva-home', profile: 'eva-000',
    credentialsPath: '/synthetic/credentials.yaml', credentialSharingVersion: 1 });
});

test('lifecycle failure rejects composite activation before DM is applied', async () => {
  const failure = new Error('lifecycle initialization failed');
  let lifecycleCalls = 0, dmCalls = 0;
  const apply = await isolated(() => { lifecycleCalls++; throw failure; }, () => { dmCalls++; });
  await assert.rejects(apply({}, { profile: 'eva-000' }), error => error === failure);
  assert.equal(lifecycleCalls, 1);
  assert.equal(dmCalls, 0);
});

test('DM failure rejects composite activation without silently retrying either contribution', async () => {
  const failure = new Error('DM initialization failed');
  let lifecycleCalls = 0, dmCalls = 0;
  const apply = await isolated(() => { lifecycleCalls++; }, async () => { dmCalls++; throw failure; });
  await assert.rejects(apply({}, { profile: 'eva-000' }), error => error === failure);
  assert.equal(lifecycleCalls, 1);
  assert.equal(dmCalls, 1);
});

test('published EVA bundle includes the composite and its two statically imported modules', async () => {
  const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
  for (const file of ['child-entry.js', 'child.js', 'dm-child.js']) {
    assert.ok(manifest.files.includes(file), `${file} must survive package publication`);
    await readFile(new URL(`../${file}`, import.meta.url), 'utf8');
  }
});
