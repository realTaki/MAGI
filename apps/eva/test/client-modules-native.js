// Read-only native discovery probe: execute Cordis/ClientModuleRegistry inside
// MAGI Electron for ASAR support, never a Host or a plugin apply function.
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const packageRoot = process.argv[2] ? resolve(process.argv[2]) : fileURLToPath(new URL('..', import.meta.url));
const dmRoot = process.argv[3] ? resolve(process.argv[3]) : fileURLToPath(new URL('../../../packages/dm', import.meta.url));
const electron = '/Applications/MAGI.app/Contents/MacOS/MAGI';
const nativeRoot = '/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/';
const script = `
  const assert = require('node:assert/strict');
  const { existsSync } = require('node:fs');
  const { join } = require('node:path');
  const { pathToFileURL } = require('node:url');
  const { Context } = require(${JSON.stringify(nativeRoot)} + 'cordis/lib/index.js');
  const { ClientModuleRegistry } = require(${JSON.stringify(nativeRoot)} + 'dsh-client-modules/lib/index.js');
  const { composeEntries } = require(${JSON.stringify(nativeRoot)} + 'dsh-app-boot/lib/index.js');
  const { ModuleLoader } = require(${JSON.stringify(nativeRoot)} + 'cordis-plugin-loader/lib/index.js');
  const root = ${JSON.stringify(packageRoot)}, dmRoot = ${JSON.stringify(dmRoot)};
  const entry = (root, file) => ({ options: { name: join(root, file) }, fiber: {}, disabled: false,
    parent: { tree: { ctx: { baseUrl: pathToFileURL(join(root, 'package.json')).href } } } });
  const child = entry(root, 'child.js'), dm = entry(dmRoot, 'index.js');
  for (const item of [child, dm]) assert.ok(existsSync(item.options.name));
  const ctx = new Context();
  let entries = [];
  ctx.provide('loader', { entries: () => entries });
  const registry = new ClientModuleRegistry(ctx);
  assert.equal(registry.graph().entries.length, 0);
  entries = [child, dm];
  const errors = [];
  for (const item of entries) assert.equal(registry.processOne(item.options.name, error => errors.push(error)), true);
  assert.deepEqual(errors, [], 'distinct native packages must not duplicate one Client id');
  assert.equal(registry.clientPath('@local/eva'), join(root, 'client.js'));
  assert.equal(registry.clientPath('@magi/dm'), join(dmRoot, 'client.js'));
  const graph = registry.compose();
  const eva = graph.entries.filter(row => row.id === '@local/eva');
  const business = graph.entries.filter(row => row.id === '@magi/dm');
  assert.equal(eva.length, 1);
  assert.equal(business.length, 1);
  for (const dependency of ['@deepseek-ai/dsh-client-ui-conversation', '@deepseek-ai/dsh-client-ui-chat']) {
    assert.ok(business[0].inject.includes(dependency));
    assert.ok(!eva[0].inject.includes(dependency), 'EVA no longer owns DM metadata dependencies');
  }
  // Real native patch semantics: name-less disables preserve the original specifier.
  // Both selected modern EVA bundles and legacy bundles without a DM row must work.
  const nativeInternal = ModuleLoader.fromInternal();
  assert.ok(nativeInternal, 'native resolver is required for bare @magi/dm discovery');
  const baseUrl = pathToFileURL(join(root, 'package.json')).href;
  const patchProbe = (hasOriginal, disableOriginal) => {
    const warnings = [];
    const initial = hasOriginal ? [{ id: 'magi-dm', name: '@magi/dm' }] : [];
    const rows = composeEntries([
      [{ insert: initial }],
      [{ insert: [{ id: 'magi-dm-child', name: join(dmRoot, 'index.js') }] },
       ...(disableOriginal ? [{ id: 'magi-dm', disabled: true }] : [])],
    ], (...warning) => warnings.push(warning));
    if (hasOriginal && disableOriginal) {
      assert.equal(rows.find(row => row.id === 'magi-dm').name, '@magi/dm');
      assert.equal(rows.find(row => row.id === 'magi-dm').disabled, true);
      assert.equal(warnings.length, 0);
    }
    if (!hasOriginal && disableOriginal) assert.equal(warnings.length, 1, 'legacy missing row only warns');
    const probeCtx = new Context();
    let probeEntries = [];
    probeCtx.provide('loader', { internal: nativeInternal, entries: () => probeEntries });
    const probeRegistry = new ClientModuleRegistry(probeCtx);
    probeEntries = rows.map(row => ({ options: row, fiber: {}, disabled: !!row.disabled,
      parent: { tree: { ctx: { baseUrl } } } }));
    const errors = [];
    for (const name of new Set(rows.map(row => row.name))) probeRegistry.processOne(name, error => errors.push(error));
    if (hasOriginal && !disableOriginal) {
      assert.ok(errors.some(error => /multiple active Loader sources/.test(error.message)), 'control must detect two active DM sources');
    } else {
      assert.deepEqual(errors, []);
      assert.equal(probeRegistry.compose().entries.filter(row => row.id === '@magi/dm').length, 1);
      assert.equal(probeRegistry.clientPath('@magi/dm'), join(dmRoot, 'client.js'));
    }
  };
  patchProbe(true, false); // Positive duplicate-source control.
  patchProbe(true, true);  // Modern selected bundle's DM source is disabled.
  patchProbe(false, true); // Older selected EVA bundle has no DM source to disable.
  process.stdout.write(JSON.stringify({ nativePatchComposition: true, disabledOriginalDmExcluded: true,
    legacyMissingDmOnlyWarns: true, duplicateActiveDmControl: true, nativeContext: true, nativeRegistry: true,
    metadataOnly: true, distinctEntriesDiscovered: true, clientPathsMatched: true,
    conversationDependency: true, chatDependency: true }) + '\\n');
`;
process.stdout.write(execFileSync(electron, ['--expose-internals', '-e', script], {
  env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, encoding: 'utf8', maxBuffer: 64 * 1024,
}));
