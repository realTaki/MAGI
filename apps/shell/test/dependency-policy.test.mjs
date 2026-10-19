import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const require = createRequire(import.meta.url);
const lock = JSON.parse(readFileSync(path.join(root, 'package-lock.json'), 'utf8'));
const semver = require('semver');

test('the workspace has one authoritative install lock and release cache key', () => {
  for (const app of ['asp', 'shell']) {
    assert.equal(existsSync(path.join(root, `apps/${app}/package-lock.json`)), false);
  }
  for (const platform of ['macos', 'windows']) {
    const workflow = readFileSync(path.join(root, `.github/workflows/${platform}-client-release.yml`), 'utf8');
    assert.doesNotMatch(workflow, /apps\/(?:asp|shell)\/package-lock\.json/);
    assert.match(workflow, /hashFiles\('package-lock\.json'/);
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

test('the legacy Drizzle TypeScript loader works with its scoped esbuild override', async () => {
  const loaderRequire = createRequire(require.resolve('@esbuild-kit/core-utils'));
  assert.equal(loaderRequire('esbuild').version, '0.25.12');
  const { transformSync, transform } = require('@esbuild-kit/core-utils');
  const source = 'export const answer: number = 42;';
  const cjs = transformSync(source, path.join(root, 'dependency-policy-fixture.cts'));
  const context = { module: { exports: {} }, exports: {} };
  vm.runInNewContext(cjs.code, context);
  assert.equal(context.module.exports.answer, 42);
  const esm = await transform(source, path.join(root, 'dependency-policy-fixture.mts'));
  const loaded = await import(`data:text/javascript;base64,${Buffer.from(esm.code).toString('base64')}`);
  assert.equal(loaded.answer, 42);
});
