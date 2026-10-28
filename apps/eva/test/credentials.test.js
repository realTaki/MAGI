import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { credentialStorePath } from '../credentials.js';

// These isolated packages test the EVA discovery boundary, not the real
// provider's parsing, permissions or cross-process credential behavior.
async function fixture(t, config = {}) {
  const base = await mkdtemp(join(tmpdir(), 'eva-credential-path-test-'));
  t.after(async () => {
    assert.ok(base.startsWith(join(tmpdir(), 'eva-credential-path-test-')));
    await rm(base, { recursive: true, force: true });
  });
  const anchor = join(base, 'package.json');
  await writeFile(anchor, '{"type":"module"}\n');
  const modules = join(base, 'node_modules', '@deepseek-ai');
  const providerDir = join(modules, 'dsh-credentials-local');
  const pathsDir = join(modules, 'dsh-home-paths');
  await mkdir(providerDir, { recursive: true });
  await mkdir(pathsDir, { recursive: true });
  for (const dir of [providerDir, pathsDir]) {
    await writeFile(join(dir, 'package.json'), '{"type":"module","exports":"./index.js"}\n');
  }
  await writeFile(join(pathsDir, 'index.js'), `
    import { homedir } from 'node:os';
    import { join, resolve } from 'node:path';
    export const calls = [];
    export function resolveDshHome(configured) {
      calls.push(configured);
      const env = process.env.DSH_HOME;
      let value = configured ?? (env?.trim() ? env : join(homedir(), '.dsh'));
      if (value === '~') value = homedir();
      else if (value.startsWith('~/') || value.startsWith('~\\\\')) value = join(homedir(), value.slice(2));
      return resolve(value);
    }
  `);
  await writeFile(join(providerDir, 'index.js'), `
    import { join, resolve } from 'node:path';
    import { resolveDshHome } from '@deepseek-ai/dsh-home-paths';
    export const specCalls = [];
    let override;
    export function setResolvedFilename(value) { override = { value }; }
    export function resolveSpec(config) {
      specCalls.push(config);
      return { filename: override ? override.value : resolve(config.path ?? join(resolveDshHome(config.dshHome), '.credentials.yaml')),
        watch: config.watch ?? true, debounceMs: config.debounceMs ?? 100 };
    }
    export class LocalCredentialProvider {
      constructor(config) { this.config = config; }
      get spec() { throw new Error('Do not read provider private state'); }
      get values() { throw new Error('Do not inspect credential values'); }
      get records() { throw new Error('Do not inspect credential records'); }
      resolve() { throw new Error('Do not resolve credentials'); }
      readRecord() { throw new Error('Do not read credential records'); }
      listRecords() { throw new Error('Do not enumerate credentials'); }
      describe() { throw new Error('Do not inspect credential sources'); }
    }
    export default LocalCredentialProvider;
  `);
  const provider = await import(pathToFileURL(join(providerDir, 'index.js')).href);
  const paths = await import(pathToFileURL(join(pathsDir, 'index.js')).href);
  const ctx = { profileContext: { installAnchor: anchor, home: join(base, 'not-the-provider-home') }, credentials: new provider.default(config) };
  return { base, ctx, paths, provider };
}

function environment(t, value) {
  const previous = process.env.DSH_HOME;
  if (value === undefined) delete process.env.DSH_HOME;
  else process.env.DSH_HOME = value;
  t.after(() => {
    if (previous === undefined) delete process.env.DSH_HOME;
    else process.env.DSH_HOME = previous;
  });
}

test('credential discovery uses the active official provider custom path without reading secrets', async (t) => {
  const f = await fixture(t, { path: 'absent-credential-fixture/store.yaml', dshHome: '/ignored-home' });
  assert.equal(await credentialStorePath(f.ctx), resolve('absent-credential-fixture/store.yaml'));
  assert.equal(f.paths.calls.length, 0, 'explicit store path takes precedence over the home resolver');
});

test('credential discovery preserves an absolute custom path including spaces', async (t) => {
  const f = await fixture(t);
  f.ctx.credentials.config = { path: join(f.base, 'custom directory', 'store.yaml') };
  assert.equal(await credentialStorePath(f.ctx), f.ctx.credentials.config.path);
});

test('credential discovery delegates a custom dshHome to the home resolver, not profileContext.home', async (t) => {
  environment(t, '/ignored-env-home');
  const f = await fixture(t, { dshHome: '~/eva-credential-fixture-home' });
  assert.equal(await credentialStorePath(f.ctx), join(homedir(), 'eva-credential-fixture-home', '.credentials.yaml'));
  assert.deepEqual(f.paths.calls, ['~/eva-credential-fixture-home']);
});

test('credential discovery uses the provider environment default when config omits its location', async (t) => {
  const f = await fixture(t);
  environment(t, join(f.base, 'environment-home'));
  assert.equal(await credentialStorePath(f.ctx), join(f.base, 'environment-home', '.credentials.yaml'));
});

for (const value of [undefined, '   ']) {
  test(`credential discovery delegates the ${value === undefined ? 'absent' : 'blank'} environment default to the home resolver`, async (t) => {
    environment(t, value);
    const f = await fixture(t);
    assert.equal(await credentialStorePath(f.ctx), join(homedir(), '.dsh', '.credentials.yaml'));
  });
}

test('credential discovery does not expand a tilde in explicit path independently of the provider', async (t) => {
  const f = await fixture(t, { path: '~/literal-store.yaml' });
  assert.equal(await credentialStorePath(f.ctx), resolve('~/literal-store.yaml'));
});

test('credential discovery rejects a custom provider even if its config resembles the local provider', async (t) => {
  const f = await fixture(t);
  f.ctx.credentials = { config: { path: join(f.base, 'not-authoritative.yaml') } };
  await assert.rejects(credentialStorePath(f.ctx));
});

test('credential discovery fails closed when no credential provider is available', async (t) => {
  const f = await fixture(t);
  delete f.ctx.credentials;
  await assert.rejects(credentialStorePath(f.ctx));
});

test('credential discovery requires an actual parent installation anchor', async () => {
  await assert.rejects(credentialStorePath({ credentials: {}, profileContext: {} }));
});

test('credential discovery delegates the exact active config to the exported provider resolver', async (t) => {
  const f = await fixture(t, { path: '/not-the-resolved-path', debounceMs: 123 });
  const authoritative = join(f.base, 'resolver-owned-location.yaml');
  f.provider.setResolvedFilename(authoritative);
  assert.equal(await credentialStorePath(f.ctx), authoritative);
  assert.equal(f.provider.specCalls.length, 1);
  assert.equal(f.provider.specCalls[0], f.ctx.credentials.config);
});

test('credential discovery rejects disabled watching rather than promising stale shared login', async (t) => {
  const f = await fixture(t, { path: join(tmpdir(), 'not-read.yaml'), watch: false });
  await assert.rejects(credentialStorePath(f.ctx));
});

for (const filename of ['relative-location.yaml', '', undefined, 42]) {
  test(`credential discovery rejects a nonabsolute or invalid resolver result: ${String(filename)}`, async (t) => {
    const f = await fixture(t);
    f.provider.setResolvedFilename(filename);
    await assert.rejects(credentialStorePath(f.ctx));
  });
}
