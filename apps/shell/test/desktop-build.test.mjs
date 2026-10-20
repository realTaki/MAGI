import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { buildDesktop } from '../scripts/build.mjs';
import { assertBootstrapEntries, requiredBootstrapEntries } from '../scripts/package-entries.mjs';

const directory = path.resolve('/project/apps/shell');
const node = path.join(directory, 'runtime/bin/node');
const base = {
  directory, platform: 'linux', env: { PATH: '/prior', EXAMPLE: 'kept' }, args: ['--linux', 'AppImage'],
  exists: () => true, resolve: (name) => path.join('/project/node_modules', name),
};

test('packaging runs runtime preparation, root compilation, and UI build before builder', () => {
  const calls = [];
  buildDesktop({ ...base, run: (...args) => calls.push(args) });
  assert.equal(calls.length, 4);
  assert.ok(calls.every(([command]) => command === node));
  assert.deepEqual(calls[0][1], [path.join(directory, 'scripts/prepare-runtime.mjs')]);
  assert.deepEqual(calls[1][1].slice(1), ['run', 'build', '--', '--force']);
  assert.equal(calls[1][2].cwd, path.resolve(directory, '../..'));
  assert.deepEqual(calls[2][1].slice(1), ['run', 'build:ui']);
  assert.equal(calls[2][2].cwd, directory);
  assert.deepEqual(calls[3][1], [path.join('/project/node_modules', 'electron-builder/out/cli/cli.js'), '--linux', 'AppImage']);
  for (const call of calls.slice(1)) {
    assert.equal(call[2].env.PATH.split(path.delimiter)[0], path.join(directory, 'runtime/bin'));
    assert.equal(call[2].env.EXAMPLE, 'kept');
  }
});

for (const stage of [0, 1, 2]) test(`prerequisite failure at stage ${stage} prevents packaging`, () => {
  const calls = [];
  assert.throws(() => buildDesktop({ ...base, run: (...args) => {
    calls.push(args);
    if (calls.length === stage + 1) throw new Error('failed prerequisite');
  } }), /failed prerequisite/);
  assert.equal(calls.length, stage + 1);
  assert.ok(calls.every(([, args]) => !args[0].includes('electron-builder')));
});

test('missing prepared runtime uses only repository-owned Node to bootstrap', () => {
  const calls = [];
  const packageNode = path.join('/project/node_modules/node/bin/node');
  buildDesktop({ ...base, exists: (file) => file === packageNode, run: (...args) => calls.push(args) });
  assert.equal(calls[0][0], packageNode);
  assert.ok(calls.slice(1).every(([command]) => command === node));
  assert.throws(() => buildDesktop({ ...base, exists: () => false, run: () => assert.fail('must not launch anything') }), /MAGI-owned Node is missing/);
});

test('Windows packaging uses the owned node.exe for every stage', () => {
  const calls = [];
  buildDesktop({ ...base, platform: 'win32', run: (...args) => calls.push(args) });
  assert.ok(calls.every(([command]) => command === path.join(directory, 'runtime/bin/node.exe')));
});

test('native archive verification rejects missing compiled workspace bootstrap dependencies', () => {
  assert.doesNotThrow(() => assertBootstrapEntries(requiredBootstrapEntries));
  for (const missing of ['/node_modules/@magi/bus/dist/index.js', '/node_modules/@magi/bus/dist/replaceMarker.js', '/node_modules/@magi/runtime/dist/protocol.js']) {
    assert.throws(() => assertBootstrapEntries(requiredBootstrapEntries.filter((file) => file !== missing)), /missing from package/);
  }
});
