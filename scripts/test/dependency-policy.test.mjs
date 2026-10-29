import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const require = createRequire(import.meta.url);
const lock = JSON.parse(readFileSync(path.join(root, 'package-lock.json'), 'utf8'));
const semver = require('semver');

test('the workspace has one authoritative install lock and validation cache key', () => {
  for (const app of ['asp', 'shell']) {
    assert.equal(existsSync(path.join(root, `apps/${app}/package-lock.json`)), false);
  }
  for (const platform of ['linux', 'macos', 'windows']) {
    const workflow = readFileSync(path.join(root, `.github/workflows/${platform}-validation.yml`), 'utf8');
    assert.doesNotMatch(workflow, /apps\/(?:asp|shell)\/package-lock\.json/);
    assert.match(workflow, /cache-dependency-path: package-lock\.json/);
    assert.equal(existsSync(path.join(root, `.github/workflows/${platform}-client-release.yml`)), false);
    assert.doesNotMatch(workflow, /electron-builder|upload-artifact|build:ui/);
  }
});

test('resolved build dependencies exclude the patched advisory ranges', () => {
  for (const [location, pkg] of Object.entries(lock.packages)) {
    if (location.endsWith('/esbuild')) assert.ok(semver.gte(pkg.version, '0.25.0'), location);
    if (location.endsWith('/undici')) {
      assert.ok(!semver.satisfies(pkg.version, '<6.28.1 || >=7.0.0 <7.29.1'), location);
    }
    if (location.endsWith('/brace-expansion')) {
      assert.ok(!semver.satisfies(pkg.version, '<1.1.21 || >=2.0.0 <2.1.7 || >=4.0.0 <5.0.12'), location);
    }
  }
});

for (const retired of ['channel-a2a', 'channel-tasks', 'launcher', 'skills']) {
  test(`retired ${retired} is absent and has no current product dependency`, () => {
    assert.equal(existsSync(path.join(root, 'packages', retired)), false);
    for (const location of ['apps/eva', 'apps/magi-shell', 'packages/dm', 'packages/memory']) {
      const manifest = JSON.parse(readFileSync(path.join(root, location, 'package.json'), 'utf8'));
      for (const key of ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']) {
        assert.equal(manifest[key]?.[`@magi/${retired}`], undefined, `${location} ${key}`);
      }
    }
  });
}

// Upstream's Electron advisory regression targeted the retired desktop app.
// The current product excludes that entire wrapper/packaging graph instead.
test('retired Electron advisory surface is not restored by dependency updates', () => {
  assert.equal(existsSync(path.join(root, 'apps/shell/package.json')), false);
  for (const [location, pkg] of Object.entries(lock.packages)) {
    assert.ok(!location.endsWith('/electron'), location);
    for (const key of ['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies']) {
      assert.equal(pkg[key]?.electron, undefined, `${location} ${key}`);
      assert.equal(pkg[key]?.['electron-builder'], undefined, `${location} ${key}`);
    }
  }
});

test('only current product workspaces and owned tooling are installable', () => {
  const manifest = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'));
  const workspaces = ['apps/eva', 'apps/magi-shell', 'packages/dm', 'packages/memory'];
  assert.deepEqual(manifest.workspaces, workspaces);
  assert.deepEqual(lock.packages[''].workspaces, workspaces);
  assert.equal(manifest.devDependencies.node, '24.21.0');
  assert.equal(lock.packages['node_modules/node'].version, manifest.devDependencies.node);
  assert.equal(lock.packages['node_modules/semver'].version, manifest.devDependencies.semver);
  for (const location of Object.keys(lock.packages)) {
    if (location.startsWith('apps/') || location.startsWith('packages/')) assert.ok(workspaces.includes(location), location);
    assert.doesNotMatch(location, /(?:@magi\/(?:providers|shell|launcher|skills|channel-a2a|channel-tasks)|@earendil-works\/pi-ai|electron|@esbuild-kit)/, location);
  }
  for (const location of workspaces) assert.equal(existsSync(path.join(root, location, 'package-lock.json')), false);
  for (const name of ['build', 'db:generate', 'test:legacy', 'verify:legacy']) assert.equal(manifest.scripts[name], undefined, name);
});
