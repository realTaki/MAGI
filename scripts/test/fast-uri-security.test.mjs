import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const productLock = JSON.parse(readFileSync(new URL('../../package-lock.json', import.meta.url), 'utf8'));

// The DSH refactor removed the Electron/provider dependency graph that supplied
// this parser. Keep the upstream behavior regressions below for any deliberate
// reintroduction, but do not reinstall a retired dependency just to run its tests.
if (!productLock.packages['node_modules/fast-uri']) {
  test('the retired fast-uri parser is absent from the complete product dependency graph', () => {
    for (const [location, pkg] of Object.entries(productLock.packages)) {
      assert.ok(!location.endsWith('/fast-uri'), location);
      for (const key of ['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies']) {
        assert.equal(pkg[key]?.['fast-uri'], undefined, `${location} ${key}`);
      }
    }
  });
} else {
const fastURI = require('fast-uri');

// These are finite, local parser checks; they never navigate or make requests.
// The 3.1.9 release fixes GHSA-w74x-v6f9-j3xg, GHSA-wjr9-j795-jxcr,
// GHSA-xhq7-xmqj-769h, and GHSA-333x-w4r6-6crp without changing major versions.
test('the installed fast-uri matches the authoritative lock and excludes the vulnerable 3.x range', () => {
  const lock = JSON.parse(readFileSync(new URL('../../package-lock.json', import.meta.url), 'utf8'));
  const installed = require('fast-uri/package.json');
  const semver = require('semver');
  assert.equal(installed.version, lock.packages['node_modules/fast-uri'].version);
  assert.equal(semver.satisfies(installed.version, '>=3.0.0 <3.1.9'), false);
});

test('fast-uri does not turn percent-encoded schemes into live schemes', () => {
  for (const input of ['%6Aavascript:alert(1)', '%u006Aavascript:alert(1)', 'ht%74ps://example.com/']) {
    assert.equal(fastURI.parse(input).error, 'URI scheme is malformed.');
    assert.equal(fastURI.normalize(input), input);
    assert.throws(() => fastURI.resolve('https://example.com/app/', input), /URI scheme is malformed/);
  }
  assert.equal(fastURI.equal('%68ttp://a/', 'http://a/'), false);
});

test('fast-uri rejects ambiguous domain hosts instead of silently rewriting them', () => {
  for (const input of ['http://a@b@evil.example/', 'http://exa\tmple.example/', 'https://exa\nmple.example/']) {
    assert.equal(fastURI.parse(input).error, 'URI host is malformed.');
    assert.equal(fastURI.normalize(input), input);
    assert.equal(fastURI.equal(input, input), false);
    assert.throws(() => fastURI.resolve(input, 'child'), /URI host is malformed/);
  }
});

test('fast-uri preserves relative reference resolution when normalizing dot segments', () => {
  const base = 'https://example.com/app/nested/index';
  for (const reference of ['../x', 'a/../b', '../../x', './foo:bar']) {
    assert.equal(fastURI.resolve(base, fastURI.normalize(reference)), fastURI.resolve(base, reference));
  }
  assert.equal(fastURI.equal('../x', 'x'), false);
});

test('fast-uri suffix references escape a colon in the first path segment', () => {
  for (const input of ['javascript:alert(1)', 'data:text/plain,value', 'vbscript:msgbox(1)']) {
    const normalized = fastURI.normalize(input, { reference: 'suffix', scheme: 'x' });
    assert.match(normalized, /^[^:]+%3A/);
    assert.equal(fastURI.parse(normalized).scheme, undefined);
  }
  assert.equal(fastURI.serialize({ path: 'foo:bar' }, { reference: 'suffix' }), 'foo%3Abar');
});

test('fast-uri keeps ordinary absolute URLs and safe relative references compatible', () => {
  assert.equal(fastURI.normalize('HTTPS://EXAMPLE.COM:443/a/./b'), 'https://example.com/a/b');
  assert.equal(fastURI.resolve('https://example.com/app/index', '../image.png'), 'https://example.com/image.png');
  assert.equal(fastURI.equal('https://EXAMPLE.com:443/a', 'https://example.com/a'), true);
  assert.equal(fastURI.serialize({ scheme: 'https', host: 'example.com', path: '/a%2Fb' }), 'https://example.com/a%2Fb');
});
}
