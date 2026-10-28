// Standalone, read-only native-contract probe. No Host/plugin entry is executed.
// Run with MAGI-owned Node; native Cordis and ClientModuleRegistry execute inside
// MAGI Electron solely for ASAR support. Only the Loader entry inventory is fake.
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const packageRoot = process.argv[2] ? resolve(process.argv[2]) : fileURLToPath(new URL('..', import.meta.url));
const electron = '/Applications/MAGI.app/Contents/MacOS/MAGI';
const nativeRoot = '/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/';
const script = `
  const assert = require('node:assert/strict');
  const { existsSync } = require('node:fs');
  const { join } = require('node:path');
  const { pathToFileURL } = require('node:url');
  const { Context } = require(${JSON.stringify(nativeRoot)} + 'cordis/lib/index.js');
  const { ClientModuleRegistry } = require(${JSON.stringify(nativeRoot)} + 'dsh-client-modules/lib/index.js');
  const root = ${JSON.stringify(packageRoot)};
  const baseUrl = pathToFileURL(join(root, 'package.json')).href;
  const entry = name => ({ options: { name: join(root, name) }, fiber: {}, disabled: false,
    parent: { tree: { ctx: { baseUrl } } } });
  const make = () => {
    const ctx = new Context();
    let entries = [];
    // No internal resolver: exercise the registry's documented absolute-path
    // fallback, which locates the nearest named manifest from the module URL.
    ctx.provide('loader', { entries: () => entries });
    const registry = new ClientModuleRegistry(ctx);
    assert.equal(registry.graph().entries.length, 0);
    return { registry, set(value) { entries = value; } };
  };
  const duplicate = make();
  const child = entry('child.js'), dm = entry('dm-child.js');
  const errors = [];
  duplicate.set([child]);
  assert.equal(duplicate.registry.processOne(child.options.name, error => errors.push(error)), true);
  assert.equal(errors.length, 0);
  assert.equal(duplicate.registry.clientPath('@local/eva'), join(root, 'client.js'));
  duplicate.set([child, dm]);
  duplicate.registry.processOne(dm.options.name, error => errors.push(error));
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /resolves from multiple active Loader sources/);
  const single = make();
  const wrapper = entry('child-entry.js');
  assert.ok(existsSync(wrapper.options.name), 'actual union wrapper must exist');
  single.set([wrapper]);
  const singleErrors = [];
  assert.equal(single.registry.processOne(wrapper.options.name, error => singleErrors.push(error)), true);
  assert.equal(singleErrors.length, 0);
  assert.equal(single.registry.clientPath('@local/eva'), join(root, 'client.js'));
  const graph = single.registry.compose();
  const row = graph.entries.find(row => row.id === '@local/eva');
  assert.ok(row);
  assert.ok(row.inject.includes('@deepseek-ai/dsh-client-ui-conversation'));
  assert.ok(row.inject.includes('@deepseek-ai/dsh-client-ui-chat'));
  process.stdout.write(JSON.stringify({ nativeContext: true, nativeRegistry: true,
    metadataOnly: true, duplicateRejected: true, singleEntryDiscovered: true,
    clientPathMatched: true, conversationDependency: true, chatDependency: true }) + '\\n');
`;
const output = execFileSync(electron, ['--expose-internals', '-e', script], {
  env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, encoding: 'utf8', maxBuffer: 64 * 1024,
});
process.stdout.write(output);
